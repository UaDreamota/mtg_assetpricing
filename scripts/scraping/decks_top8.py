import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from html import unescape
import json
import re
import sys
import threading
import time
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data" / "mtgtop8"
SEARCH_URL = "https://www.mtgtop8.com/search"
DECK_EXPORT_URL = "https://www.mtgtop8.com/dec"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)

DECK_LINK_RE = re.compile(
    r"href\s*=\s*[\"']?(event\?e=\d+&d=\d+&f=[^\"'\s>#]+)",
    re.IGNORECASE,
)
TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
COUNT_RE = re.compile(r"^\s*(\d+)\s*$")
SIDEBOARD_SPLIT_RE = re.compile(r"(?:SIDEBOARD|Sideboard|SB:)", re.IGNORECASE)
DEC_CARD_RE = re.compile(
    r"^(?:(SB)\s*:\s*)?(\d+)\s+(?:\[[^\]]*\]\s*)?(.+?)\s*$",
    re.IGNORECASE,
)
TOTAL_RE = re.compile(r"([0-9][0-9,]*)\s+decks matching", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
DECK_URL_ID_RE = re.compile(r"[?&]e=(\d+)&d=(\d+)&f=([^&#]+)")
RANK_NUMBER_RE = re.compile(r"\d+")
PART_RE = re.compile(r"^part-(\d{6})-(\d{6})\.parquet$")
THREAD_STATE = threading.local()

DECK_SCHEMA = pa.schema(
    [
        ("deck_id", pa.int32()),
        ("event_id", pa.int32()),
        ("format_code", pa.string()),
        ("archetype", pa.string()),
        ("player", pa.string()),
        ("format", pa.string()),
        ("event_name", pa.string()),
        ("level", pa.string()),
        ("rank", pa.string()),
        ("event_date", pa.date32()),
    ]
)

CARD_SCHEMA = pa.schema(
    [
        ("deck_id", pa.int32()),
        ("event_id", pa.int32()),
        ("is_sideboard", pa.bool_()),
        ("quantity", pa.int8()),
        ("card_name", pa.string()),
    ]
)


class HttpFetchError(RuntimeError):
    def __init__(self, url: str, status_code: int, reason: str, body: str):
        self.url = url
        self.status_code = status_code
        self.reason = reason
        self.body = body
        super().__init__(
            f"Failed to fetch {url} "
            f"(http_status={status_code}, reason={reason}, body={body!r})"
        )


@dataclass(frozen=True)
class SearchRow:
    deck_id: int
    event_id: int
    format_code: str
    archetype: str
    player: str
    format_name: str
    event_name: str
    level: str
    rank: str
    event_date: datetime.date
    deck_url: str


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {seconds:.0f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {seconds:.0f}s"


def build_session(pool_size: int = 16) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=pool_size,
        pool_maxsize=pool_size,
        max_retries=retry,
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def get_thread_session() -> requests.Session:
    session = getattr(THREAD_STATE, "session", None)
    if session is None:
        session = build_session()
        THREAD_STATE.session = session
    return session


def fetch_text(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str],
    retries: int,
    backoff: float,
    request_delay: float,
    timeout: float,
) -> str:
    last_error: Exception | None = None
    method = "POST" if data is not None else "GET"
    for attempt in range(1, retries + 1):
        try:
            response = session.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                timeout=timeout,
            )
            if not response.ok:
                snippet = " ".join(response.text[:160].split())
                raise HttpFetchError(response.url, response.status_code, response.reason, snippet)
            if request_delay:
                time.sleep(request_delay)
            return response.text
        except (requests.RequestException, HttpFetchError) as error:
            last_error = error
            if attempt == retries:
                break
            time.sleep(backoff * attempt)
    if isinstance(last_error, HttpFetchError):
        raise last_error
    raise RuntimeError(
        f"Failed to fetch {url} via {method} after {retries} attempts: {last_error!r}"
    ) from last_error


@lru_cache(maxsize=512)
def parse_event_date(value: str) -> datetime.date:
    return datetime.strptime(value.strip(), "%d/%m/%y").date()


def parse_link_ids(deck_url: str) -> tuple[int, int, str]:
    match = DECK_URL_ID_RE.search(deck_url)
    if match:
        return int(match.group(1)), int(match.group(2)), match.group(3)
    query = parse_qs(urlparse(deck_url).query)
    return int(query["e"][0]), int(query["d"][0]), query["f"][0]


def rank_can_be_at_most(rank: str, max_rank: int | None) -> bool:
    if max_rank is None:
        return True
    match = RANK_NUMBER_RE.search(rank)
    return match is not None and int(match.group()) <= max_rank


def strip_html_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", value))).strip()


def extract_deck_links_from_rows(html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for row_html in TR_RE.findall(html):
        match = DECK_LINK_RE.search(row_html)
        if not match:
            continue
        # pandas.read_html drops image-only cells; retain MTGTop8's event-level
        # star count directly from the row HTML.
        level_stars = row_html.lower().count("graph/star.png")
        links.append((urljoin(SEARCH_URL, unescape(match.group(1))), str(level_stars)))
    return links


def parse_search_page(html: str) -> tuple[list[SearchRow], int | None]:
    rows: list[SearchRow] = []
    for row_html in TR_RE.findall(html):
        deck_match = DECK_LINK_RE.search(row_html)
        if not deck_match:
            continue
        cells = TD_RE.findall(row_html)
        if len(cells) < 8:
            continue
        deck_url = urljoin(SEARCH_URL, unescape(deck_match.group(1)))
        event_id, deck_id, format_code = parse_link_ids(deck_url)
        level_stars = row_html.lower().count("graph/star.png")
        rows.append(
            SearchRow(
                deck_id=deck_id,
                event_id=event_id,
                format_code=format_code,
                archetype=strip_html_text(cells[1]),
                player=strip_html_text(cells[2]),
                format_name=strip_html_text(cells[3]),
                event_name=strip_html_text(cells[4]),
                level=str(level_stars),
                rank=strip_html_text(cells[6]),
                event_date=parse_event_date(strip_html_text(cells[7])),
                deck_url=deck_url,
            )
        )

    total_match = TOTAL_RE.search(html)
    total_decks = int(total_match.group(1).replace(",", "")) if total_match else None
    if not rows:
        if total_decks is not None and "compare_decks" in html:
            return [], total_decks
        snippet = strip_html_text(html[:1000])
        raise ValueError(f"Could not parse MTGTop8 search results rows from search page: {snippet!r}")
    return rows, total_decks


def extract_card_rows(fragment_html: str) -> list[tuple[int, str]]:
    cards: list[tuple[int, str]] = []
    if not fragment_html.strip():
        return cards

    for row_html in TR_RE.findall(fragment_html):
        cells = TD_RE.findall(row_html)
        if len(cells) < 2:
            continue
        count_text = WHITESPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", cells[0]))).strip()
        card_text = WHITESPACE_RE.sub(" ", unescape(TAG_RE.sub(" ", cells[1]))).strip()
        if COUNT_RE.match(count_text) and card_text:
            cards.append((int(count_text), card_text))

    if cards:
        return cards

    for table in pd.read_html(StringIO(fragment_html)):
        candidate = table.copy()
        candidate = candidate.dropna(how="all")
        if candidate.empty or candidate.shape[1] < 2:
            continue
        candidate = candidate.iloc[:, :2]
        candidate.columns = ["count", "card_name"]
        candidate["count"] = candidate["count"].astype(str).str.strip()
        candidate["card_name"] = candidate["card_name"].astype(str).str.strip()
        mask = candidate["count"].str.match(COUNT_RE.pattern) & candidate["card_name"].ne("")
        candidate = candidate.loc[mask]
        if candidate.empty:
            continue
        for _, row in candidate.iterrows():
            cards.append((int(row["count"]), unescape(str(row["card_name"])).strip()))
    return cards


def compact_cards(cards: Iterable[tuple[int, str]]) -> list[tuple[int, str]]:
    merged: dict[str, int] = {}
    for quantity, card_name in cards:
        clean_name = re.sub(r"\s+", " ", card_name).strip()
        if not clean_name:
            continue
        merged[clean_name] = merged.get(clean_name, 0) + int(quantity)
    return [(quantity, card_name) for card_name, quantity in merged.items()]


def parse_deck_page(html: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    parts = SIDEBOARD_SPLIT_RE.split(html, maxsplit=1)
    if len(parts) == 1:
        main_cards = compact_cards(extract_card_rows(parts[0]))
        return main_cards, []
    main_cards = compact_cards(extract_card_rows(parts[0]))
    sideboard_cards = compact_cards(extract_card_rows(parts[1]))
    return main_cards, sideboard_cards


def parse_deck_export(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Parse MTGTop8's compact .dec export without downloading rendered HTML."""
    main_cards: list[tuple[int, str]] = []
    sideboard_cards: list[tuple[int, str]] = []
    in_sideboard = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.lower() in {"sideboard", "sideboard:"}:
            in_sideboard = True
            continue
        match = DEC_CARD_RE.match(line)
        if not match:
            continue
        sideboard_marker, quantity, card_name = match.groups()
        target = sideboard_cards if sideboard_marker or in_sideboard else main_cards
        target.append((int(quantity), unescape(card_name).strip()))
    return compact_cards(main_cards), compact_cards(sideboard_cards)


def deck_to_rows(search_row: SearchRow, deck_export: str) -> tuple[dict, list[dict]]:
    main_cards, sideboard_cards = parse_deck_export(deck_export)
    if not main_cards and not sideboard_cards:
        raise ValueError(f"No cards parsed from {search_row.deck_url}")

    deck_row = {
        "deck_id": search_row.deck_id,
        "event_id": search_row.event_id,
        "format_code": search_row.format_code,
        "archetype": search_row.archetype,
        "player": search_row.player,
        "format": search_row.format_name,
        "event_name": search_row.event_name,
        "level": search_row.level,
        "rank": search_row.rank,
        "event_date": search_row.event_date,
    }

    card_rows: list[dict] = []
    for is_sideboard, cards in ((False, main_cards), (True, sideboard_cards)):
        for quantity, card_name in cards:
            card_rows.append(
                {
                    "deck_id": search_row.deck_id,
                    "event_id": search_row.event_id,
                    "is_sideboard": is_sideboard,
                    "quantity": quantity,
                    "card_name": card_name,
                }
            )
    return deck_row, card_rows


def checkpoint_path(output_dir: Path) -> Path:
    return output_dir / "mtgtop8_checkpoint.json"


def errors_path(output_dir: Path) -> Path:
    return output_dir / "mtgtop8_errors.csv"


def shard_dir(output_dir: Path, kind: str) -> Path:
    return output_dir / f"mtgtop8_{kind}"


def debug_dir(output_dir: Path) -> Path:
    return output_dir / "debug"


def parse_part_range(path: Path) -> tuple[int, int] | None:
    match = PART_RE.match(path.name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def get_complete_shard_ranges(output_dir: Path) -> list[tuple[int, int]]:
    deck_parts = {
        path.name: page_range
        for path in shard_dir(output_dir, "decks").glob("part-*.parquet")
        if (page_range := parse_part_range(path)) is not None
    }
    card_parts = {
        path.name: page_range
        for path in shard_dir(output_dir, "cards").glob("part-*.parquet")
        if (page_range := parse_part_range(path)) is not None
    }
    shared_names = sorted(set(deck_parts) & set(card_parts))
    return [deck_parts[name] for name in shared_names]


def get_last_completed_page_from_shards(output_dir: Path) -> int | None:
    shard_ranges = get_complete_shard_ranges(output_dir)
    if not shard_ranges:
        return None
    return max(last_page for _, last_page in shard_ranges)


def existing_output_present(output_dir: Path) -> bool:
    return any(shard_dir(output_dir, "decks").glob("part-*.parquet")) or any(
        shard_dir(output_dir, "cards").glob("part-*.parquet")
    )


def reconcile_resume_state(
    output_dir: Path,
    *,
    resume: bool,
    start_page: int,
    query: dict,
) -> tuple[bool, dict]:
    has_output = existing_output_present(output_dir)
    has_checkpoint = checkpoint_path(output_dir).exists()

    if has_output and not has_checkpoint and not resume:
        raise FileExistsError(
            f"Existing output found in {shard_dir(output_dir, 'decks')}; rerun with --resume"
        )
    if has_output and resume and not has_checkpoint:
        raise FileNotFoundError("Cannot safely resume existing shards without mtgtop8_checkpoint.json")

    if not has_output and not has_checkpoint and not resume:
        return False, {
            "last_completed_page": start_page - 1,
            "decks_written": 0,
            "cards_written": 0,
            "total_decks_seen": None,
            "query": query,
        }

    checkpoint = load_checkpoint(output_dir) if has_checkpoint else {
        "last_completed_page": start_page - 1,
        "decks_written": 0,
        "cards_written": 0,
        "total_decks_seen": None,
    }
    previous_query = checkpoint.get("query")
    if previous_query is not None and previous_query != query:
        raise ValueError(f"Resume query differs from checkpoint: {previous_query!r} != {query!r}")

    durable_last_page = get_last_completed_page_from_shards(output_dir)
    checkpoint_last_page = int(checkpoint.get("last_completed_page", start_page - 1))
    if durable_last_page is not None and durable_last_page != checkpoint_last_page:
        checkpoint["last_completed_page"] = durable_last_page
    checkpoint["query"] = query
    return True, checkpoint


def load_checkpoint(output_dir: Path) -> dict:
    path = checkpoint_path(output_dir)
    if not path.exists():
        return {
            "last_completed_page": -1,
            "decks_written": 0,
            "cards_written": 0,
            "total_decks_seen": None,
        }
    return json.loads(path.read_text())


def write_checkpoint(output_dir: Path, checkpoint: dict) -> None:
    path = checkpoint_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True))
    temporary_path.replace(path)


def write_debug_html(output_dir: Path, *, page: int, html: str) -> Path:
    path = debug_dir(output_dir) / f"search_page_{page:06d}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8", errors="ignore")
    return path


def append_errors(output_dir: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path = errors_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["page", "deck_id", "event_id", "deck_url", "error"])
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def build_search_data(
    page: int,
    *,
    format_code: str,
    date_start: str | None,
    date_end: str | None,
) -> dict[str, str]:
    # The website's form is 1-indexed and pagination must be submitted by POST.
    data = {
        "current_page": str(page + 1),
        "format": format_code,
        "compet_check[P]": "1",
        "compet_check[M]": "1",
        "compet_check[C]": "1",
        "compet_check[R]": "1",
        "MD_check": "1",
    }
    if date_start:
        data["date_start"] = date_start
    if date_end:
        data["date_end"] = date_end
    return data


def fetch_search_page(
    session: requests.Session,
    page: int,
    *,
    headers: dict[str, str],
    retries: int,
    backoff: float,
    request_delay: float,
    format_code: str,
    date_start: str | None,
    date_end: str | None,
    output_dir: Path | None = None,
    timeout: float = 30.0,
) -> tuple[str, list[SearchRow], int | None]:
    last_error: Exception | None = None
    last_html: str | None = None
    data = build_search_data(page, format_code=format_code, date_start=date_start, date_end=date_end)
    for attempt in range(1, retries + 1):
        try:
            html = fetch_text(
                session,
                SEARCH_URL,
                data=data,
                headers=headers,
                retries=1,
                backoff=backoff,
                request_delay=request_delay,
                timeout=timeout,
            )
            last_html = html
            rows, total_decks = parse_search_page(html)
            return html, rows, total_decks
        except Exception as error:
            last_error = error
            if attempt == retries:
                break
            time.sleep(backoff * attempt)
    if output_dir is not None and last_html is not None:
        debug_path = write_debug_html(output_dir, page=page, html=last_html)
        raise ValueError(f"{last_error} [debug_html={debug_path}]") from last_error
    raise last_error if last_error is not None else RuntimeError(f"Failed to parse search page {page}")


def fetch_one_deck(
    search_row: SearchRow,
    *,
    headers: dict[str, str],
    retries: int,
    backoff: float,
    request_delay: float,
    timeout: float,
) -> tuple[SearchRow, dict | None, list[dict] | None, dict | None]:
    session = get_thread_session()
    try:
        deck_export = fetch_text(
            session,
            DECK_EXPORT_URL,
            params={"d": str(search_row.deck_id)},
            headers=headers,
            retries=retries,
            backoff=backoff,
            request_delay=request_delay,
            timeout=timeout,
        )
        deck_row, card_rows = deck_to_rows(search_row, deck_export)
        return search_row, deck_row, card_rows, None
    except Exception as error:
        return (
            search_row,
            None,
            None,
            {
                "page": None,
                "deck_id": search_row.deck_id,
                "event_id": search_row.event_id,
                "deck_url": search_row.deck_url,
                "error": str(error),
            },
        )


def write_parquet_shard(
    output_dir: Path,
    *,
    first_page: int,
    last_page: int,
    deck_rows: list[dict],
    card_rows: list[dict],
) -> tuple[int, int]:
    """Atomically write a reasonably large shard for compact, safe resumes."""
    deck_rows.sort(key=lambda row: row["deck_id"])
    card_rows.sort(key=lambda row: (row["deck_id"], row["is_sideboard"], row["card_name"]))
    stem = f"part-{first_page:06d}-{last_page:06d}.parquet"
    outputs = (
        (output_dir / "mtgtop8_decks" / stem, pa.Table.from_pylist(deck_rows, schema=DECK_SCHEMA)),
        (output_dir / "mtgtop8_cards" / stem, pa.Table.from_pylist(card_rows, schema=CARD_SCHEMA)),
    )
    for final_path, table in outputs:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = final_path.with_suffix(".parquet.tmp")
        pq.write_table(
            table,
            temporary_path,
            compression="zstd",
            use_dictionary=True,
            write_statistics=True,
        )
        temporary_path.replace(final_path)
    return len(deck_rows), len(card_rows)


def scrape(
    *,
    start_page: int,
    max_pages: int | None,
    output_dir: Path,
    workers: int,
    retries: int,
    backoff: float,
    request_delay: float,
    resume: bool,
    format_code: str,
    date_start: str | None,
    date_end: str | None,
    pages_per_shard: int,
    max_rank: int | None,
    user_agent: str,
    timeout: float,
) -> None:
    started_at = time.time()
    if pages_per_shard < 1:
        raise ValueError("pages_per_shard must be at least 1")
    query = {
        "format_code": format_code,
        "date_start": date_start,
        "date_end": date_end,
        "max_rank": max_rank,
    }
    resume, checkpoint = reconcile_resume_state(
        output_dir,
        resume=resume,
        start_page=start_page,
        query=query,
    )
    page = max(start_page, int(checkpoint["last_completed_page"]) + 1)
    if resume:
        print(
            f"Resuming from page {page} "
            f"(last completed page: {checkpoint['last_completed_page']})."
        )
        sys.stdout.flush()
    remaining_pages = max_pages
    batch_first_page = page
    batch_last_page: int | None = None
    batch_decks: list[dict] = []
    batch_cards: list[dict] = []
    batch_errors: list[dict] = []
    total_errors = 0
    pages_buffered = 0
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.mtgtop8.com",
        "Pragma": "no-cache",
        "Referer": SEARCH_URL,
        "User-Agent": user_agent,
    }

    def flush_batch() -> None:
        nonlocal batch_first_page, batch_last_page, pages_buffered
        if batch_last_page is None:
            return
        if batch_decks or batch_cards:
            written_decks, written_cards = write_parquet_shard(
                output_dir,
                first_page=batch_first_page,
                last_page=batch_last_page,
                deck_rows=batch_decks,
                card_rows=batch_cards,
            )
        else:
            written_decks, written_cards = 0, 0
        append_errors(output_dir, batch_errors)
        checkpoint["last_completed_page"] = batch_last_page
        checkpoint["decks_written"] = int(checkpoint["decks_written"]) + written_decks
        checkpoint["cards_written"] = int(checkpoint["cards_written"]) + written_cards
        write_checkpoint(output_dir, checkpoint)
        batch_decks.clear()
        batch_cards.clear()
        batch_errors.clear()
        pages_buffered = 0
        batch_last_page = None

    session = build_session(pool_size=max(8, workers * 2))
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            while remaining_pages is None or remaining_pages > 0:
                print(f"Page {page}: fetching search results...", flush=True)
                try:
                    _, search_rows, total_decks = fetch_search_page(
                        session,
                        page,
                        headers=headers,
                        retries=retries,
                        backoff=backoff,
                        request_delay=request_delay,
                        format_code=format_code,
                        date_start=date_start,
                        date_end=date_end,
                        output_dir=output_dir,
                        timeout=timeout,
                    )
                except Exception as error:
                    batch_errors.append(
                        {
                            "page": page,
                            "deck_id": None,
                            "event_id": None,
                            "deck_url": None,
                            "error": str(error),
                        }
                    )
                    total_errors += 1
                    batch_last_page = page
                    pages_buffered += 1
                    print(f"Page {page}: skipped search page error: {error}", flush=True)

                    if pages_buffered >= pages_per_shard:
                        flush_batch()
                        batch_first_page = page + 1

                    page += 1
                    if remaining_pages is not None:
                        remaining_pages -= 1
                    continue

                if not search_rows:
                    flush_batch()
                    print(f"Page {page}: no rows found, stopping.")
                    break

                checkpoint["total_decks_seen"] = total_decks
                search_rows = [
                    row for row in search_rows if rank_can_be_at_most(row.rank, max_rank)
                ]
                page_decks: list[dict] = []
                page_cards: list[dict] = []
                page_errors: list[dict] = []
                print(f"Page {page}: fetching {len(search_rows)} deck exports...", flush=True)

                futures = [
                    executor.submit(
                        fetch_one_deck,
                        search_row,
                        headers=headers,
                        retries=retries,
                        backoff=backoff,
                        request_delay=request_delay,
                        timeout=timeout,
                    )
                    for search_row in search_rows
                ]
                for future in as_completed(futures):
                    search_row, deck_row, deck_cards, error_row = future.result()
                    if error_row is not None:
                        error_row["page"] = page
                        page_errors.append(error_row)
                        continue
                    page_decks.append(deck_row)
                    page_cards.extend(deck_cards or [])

                batch_decks.extend(page_decks)
                batch_cards.extend(page_cards)
                batch_errors.extend(page_errors)
                total_errors += len(page_errors)
                batch_last_page = page
                pages_buffered += 1

                elapsed = format_duration(time.time() - started_at)
                total_seen = checkpoint["total_decks_seen"]
                print(
                    f"Page {page}: rows={len(search_rows)} decks={len(page_decks)} "
                    f"cards={len(page_cards)} errors={len(page_errors)} total_seen={total_seen} elapsed={elapsed}"
                , flush=True)

                if pages_buffered >= pages_per_shard:
                    flush_batch()
                    batch_first_page = page + 1

                page += 1
                if remaining_pages is not None:
                    remaining_pages -= 1
    finally:
        flush_batch()
        session.close()
        print(f"Finished with {total_errors} total errors.", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape MTGTop8 decklists into compact Parquet tables."
    )
    parser.add_argument("--start-page", type=int, default=0, help="0-indexed MTGTop8 search page to start from.")
    parser.add_argument("--max-pages", type=int, default=None, help="Optional cap on the number of pages to scrape.")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR, help="Directory for parquet outputs and checkpoint.")
    parser.add_argument("--format-code", default="ST", help="MTGTop8 format code to request (default: ST).")
    parser.add_argument("--date-start", default=None, help="Optional earliest event date in D/M/Y format.")
    parser.add_argument("--date-end", default=None, help="Optional latest event date in D/M/Y format.")
    parser.add_argument(
        "--max-rank",
        type=int,
        default=8,
        help="Only fetch placements that can fall within this rank (default: top 8; use 0 for all).",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel deck page fetchers. Keep this modest.")
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="HTTP User-Agent header to send on requests.",
    )
    parser.add_argument("--retries", type=int, default=3, help="HTTP retries per request.")
    parser.add_argument("--backoff", type=float, default=1.5, help="Retry backoff multiplier in seconds.")
    parser.add_argument("--request-delay", type=float, default=0.25, help="Delay after successful requests in seconds.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    parser.add_argument(
        "--pages-per-shard",
        type=int,
        default=20,
        help="Search pages per Parquet part (default: 20, roughly 500 decks).",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from the checkpoint in output-dir.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    max_rank = None if args.max_rank == 0 else args.max_rank
    scrape(
        start_page=args.start_page,
        max_pages=args.max_pages,
        output_dir=args.output_dir,
        workers=args.workers,
        retries=args.retries,
        backoff=args.backoff,
        request_delay=args.request_delay,
        resume=args.resume,
        format_code=args.format_code,
        date_start=args.date_start,
        date_end=args.date_end,
        pages_per_shard=args.pages_per_shard,
        max_rank=max_rank,
        user_agent=args.user_agent,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
