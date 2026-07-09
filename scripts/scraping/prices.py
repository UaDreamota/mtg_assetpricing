import argparse
import codecs
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from html import unescape
from itertools import islice
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus, urlencode, unquote, urljoin, urlparse

import pandas as pd
import requests


BASE_DIR = Path(__file__).resolve().parent.parent
MTGGOLDFISH_BASE_URL = "https://www.mtggoldfish.com"
DEFAULT_OUTPUT = BASE_DIR / "data" / "price_history.csv"
DEFAULT_BATCH_OUTPUT = BASE_DIR / "data" / "price_histories.csv"
DEFAULT_CARDS_PATH = BASE_DIR / "data" / "default-cards-20260614090813.json"
DEFAULT_URL = "https://www.mtggoldfish.com/price/ixalan/22/legions-landing-foil#paper"
PRICE_HISTORY_URL = "https://www.mtggoldfish.com/price_history_component"
LONG_FIELDNAMES = ["card_id", "market_type", "date", "price", "card_name", "source_url"]
ERROR_FIELDNAMES = ["source_url", "error"]


HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0",
}

SET_SLUG_ALIASES = {
    "magic-2014": {"magic-2014-core-set"},
    "magic-2014-core-set": {"magic-2014"},
    "magic-2015": {"magic-2015-core-set"},
    "magic-2015-core-set": {"magic-2015"},
    "the-lost-caverns-of-ixalan": {"lost-caverns-of-ixalan"},
    "lost-caverns-of-ixalan": {"the-lost-caverns-of-ixalan"},
    "modern-masters-2017": {"modern-masters-2017-edition"},
    "modern-masters-2017-edition": {"modern-masters-2017"},
    "murders-at-karlov-manor": {"ravnica-murders-at-karlov-manor"},
    "ravnica-murders-at-karlov-manor": {"murders-at-karlov-manor"},
    "time-spiral-timeshifted": {"timeshifted"},
    "timeshifted": {"time-spiral-timeshifted"},
    "marvels-spider-man": {"marvel-super-heroes"},
    "marvel-super-heroes": {"marvels-spider-man"},
}

CARD_VARIANT_SUFFIXES = (
    "foil-etched",
    "surge-foil",
    "textured-foil",
    "prerelease-foil",
    "borderless-foil",
    "extended-foil",
    "retro-foil",
    "showcase-foil",
    "etched",
    "foil",
    "prerelease",
    "borderless",
    "extended",
    "retro",
    "showcase",
)


class FetchError(RuntimeError):
    pass


class HttpFetchError(FetchError):
    def __init__(self, url: str, status_code: int, reason: str, body: str):
        self.url = url
        self.status_code = status_code
        self.reason = reason
        self.body = body
        super().__init__(
            f"Failed to fetch {url} "
            f"(http_status={status_code}, reason={reason}, body={body!r})"
        )


def fetch_text(
    session: requests.Session,
    url: str,
    *,
    retries: int,
    backoff: float,
    request_delay: float,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=30, headers=HEADERS)
            if not response.ok:
                snippet = " ".join(response.text[:160].split())
                raise HttpFetchError(url, response.status_code, response.reason, snippet)
            if request_delay:
                time.sleep(request_delay)
            return response.text
        except (requests.RequestException, FetchError) as error:
            last_error = error
            if attempt == retries:
                break
            time.sleep(backoff * attempt)
    if isinstance(last_error, FetchError):
        raise last_error
    raise RuntimeError(f"Failed to fetch {url}") from last_error


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {seconds:.0f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes)}m {seconds:.0f}s"


def batched(values: list[str], size: int):
    iterator = iter(values)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def parse_price_url(url: str) -> tuple[str, str | None, str] | None:
    parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
    if len(parts) < 3 or parts[0] != "price":
        return None
    if len(parts) == 3:
        return parts[1], None, parts[2]
    return parts[1], parts[2], "/".join(parts[3:])


def ascii_fold(value: str) -> str:
    value = value.replace("’", "'").replace("`", "'")
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def strip_card_variant_suffixes(slug: str) -> str:
    changed = True
    while changed:
        changed = False
        for suffix in CARD_VARIANT_SUFFIXES:
            marker = f"-{suffix}"
            if slug.endswith(marker):
                slug = slug[: -len(marker)]
                changed = True
                break
    return slug


def card_name_from_price_slug(slug: str) -> str:
    slug = strip_card_variant_suffixes(slug)
    return slug.replace("-", " ").strip()


def normalized_card_words(value: str) -> str:
    value = ascii_fold(value).lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def set_slug_variants(slug: str) -> set[str]:
    variants = {slug}
    variants.update(SET_SLUG_ALIASES.get(slug, set()))
    if slug.startswith("the-"):
        variants.add(slug[4:])
    return variants


def set_slugs_match(left: str, right: str) -> bool:
    return bool(set_slug_variants(left) & set_slug_variants(right))


def card_names_compatible(candidate_name: str, desired_name: str) -> bool:
    if candidate_name == desired_name:
        return True
    return candidate_name.startswith(f"{desired_name} ") or desired_name.startswith(f"{candidate_name} ")


def possessive_query_variants(query: str) -> list[str]:
    words = query.split()
    variants: list[str] = []
    for index, word in enumerate(words):
        if len(word) <= 2 or not word.endswith("s"):
            continue
        variant_words = words.copy()
        variant_words[index] = f"{word[:-1]}'s"
        variants.append(" ".join(variant_words))
    return variants


def search_queries_from_card_slug(card_slug: str) -> list[str]:
    base_query = card_name_from_price_slug(card_slug)
    raw_queries = [base_query]
    words = base_query.split()
    for word_count in (4, 3, 2):
        if len(words) > word_count:
            raw_queries.append(" ".join(words[:word_count]))
    queries: list[str] = []
    for query in raw_queries:
        queries.append(query)
        queries.extend(possessive_query_variants(query))
    return list(dict.fromkeys(query for query in queries if query))


def search_price_links(session: requests.Session, query: str, **fetch_kwargs: object) -> list[str]:
    search_url = f"{MTGGOLDFISH_BASE_URL}/q?query_string={quote_plus(query)}"
    html = fetch_text(session, search_url, **fetch_kwargs)
    links: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r'href=["\']([^"\']*/price/[^"\']+)["\']', html):
        href = unescape(href)
        absolute_url = urljoin(MTGGOLDFISH_BASE_URL, href)
        if absolute_url not in seen:
            seen.add(absolute_url)
            links.append(absolute_url)
    return links


def fallback_price_url(
    session: requests.Session,
    url: str,
    *,
    retries: int,
    backoff: float,
    request_delay: float,
) -> str | None:
    parsed = parse_price_url(url)
    if not parsed:
        return None

    set_slug, collector_number, card_slug = parsed
    wants_foil = card_slug.endswith("-foil") or card_slug.endswith("-etched")
    search_queries = search_queries_from_card_slug(card_slug)
    if not search_queries:
        return None

    candidates: list[tuple[tuple[int, int, int, int, int, int, str], str]] = []
    seen_candidate_urls: set[str] = set()
    for query_index, search_query in enumerate(search_queries):
        desired_name = normalized_card_words(search_query)
        for candidate_url in search_price_links(
            session,
            search_query,
            retries=retries,
            backoff=backoff,
            request_delay=request_delay,
        ):
            if candidate_url in seen_candidate_urls:
                continue
            seen_candidate_urls.add(candidate_url)
            candidate = parse_price_url(candidate_url)
            if not candidate:
                continue
            candidate_set, candidate_collector, candidate_slug = candidate
            candidate_is_foil = candidate_slug.endswith("-foil") or candidate_slug.endswith("-etched")
            if candidate_is_foil != wants_foil:
                continue
            candidate_name = normalized_card_words(card_name_from_price_slug(candidate_slug))
            set_match = set_slugs_match(set_slug, candidate_set)
            collector_match = bool(collector_number and candidate_collector == collector_number)
            name_match = card_names_compatible(candidate_name, desired_name)
            if not name_match and not (set_match and collector_match):
                continue
            variant_score = 0 if strip_card_variant_suffixes(candidate_slug) == candidate_slug else 1
            score = (
                0 if set_match and collector_match else 1,
                0 if set_match else 1,
                0 if collector_match else 1,
                query_index,
                0 if candidate_name == desired_name else 1,
                variant_score,
                candidate_url,
            )
            candidates.append((score, candidate_url))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[0])[1]




def price_type_from_url(url: str) -> str:
    fragment = urlparse(url).fragment
    if fragment in {"paper", "online"}:
        return fragment
    return "paper"


def extract_card_id(page_html: str) -> tuple[str, str]:
    match = re.search(
        r'initializeCardPriceHistoryComponent\([^,]+,\s*"((?:\\.|[^"\\])*)"\s*,\s*"([^"]+)"',
        page_html,
    )
    if not match:
        raise ValueError("Could not find initializeCardPriceHistoryComponent(...) in page HTML")

    card_id = codecs.decode(match.group(1), "unicode_escape")
    price_type = codecs.decode(match.group(2), "unicode_escape")
    return card_id, price_type


def build_component_url(card_id: str, market_type: str, price_type: str) -> str:
    query = urlencode(
        {
            "card_id": card_id,
            "selector": f"#tab-{market_type}",
            "type": market_type,
            "price_type": price_type,
        }
    )
    return f"{PRICE_HISTORY_URL}?{query}"


def parse_price_history(component_js: str) -> list[list[str]]:
    header_match = re.search(r'var d = "((?:\\.|[^"\\])*)";', component_js)
    if not header_match:
        snippet = " ".join(component_js[:200].split())
        raise ValueError(f"Could not find price history CSV header in component JS (body={snippet!r})")

    rows = [[cell.strip() for cell in codecs.decode(header_match.group(1), "unicode_escape").split(",")]]
    for row_literal in re.findall(r'd \+= "(\\n(?:\\.|[^"\\])*)";', component_js):
        row = codecs.decode(row_literal, "unicode_escape").lstrip("\n")
        rows.append([cell.strip() for cell in row.split(",", maxsplit=1)])

    if len(rows) == 1:
        raise ValueError("Price history component did not contain any data rows")
    return rows


def write_csv(rows: list[list[str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def market_type_candidates(preferred_type: str, allow_market_fallback: bool) -> list[str]:
    candidates = [preferred_type]
    if allow_market_fallback:
        candidates.append("online" if preferred_type == "paper" else "paper")
    return list(dict.fromkeys(candidates))


def fetch_price_history(
    url: str,
    market_type: str | None = None,
    *,
    session: requests.Session | None = None,
    retries: int = 3,
    backoff: float = 2.0,
    request_delay: float = 1.0,
    search_fallback: bool = True,
    allow_market_fallback: bool = True,
) -> tuple[str, list[list[str]], str, str]:
    session = session or requests.Session()
    market_type = market_type or price_type_from_url(url)
    try:
        page_html = fetch_text(
            session,
            url,
            retries=retries,
            backoff=backoff,
            request_delay=request_delay,
        )
    except HttpFetchError as error:
        if not search_fallback or error.status_code != 404:
            raise
        fallback_url = fallback_price_url(
            session,
            url,
            retries=retries,
            backoff=backoff,
            request_delay=request_delay,
        )
        if not fallback_url:
            raise
        page_html = fetch_text(
            session,
            fallback_url,
            retries=retries,
            backoff=backoff,
            request_delay=request_delay,
        )
        url = fallback_url
    card_id, price_type = extract_card_id(page_html)
    parse_errors = []
    for component_market_type in market_type_candidates(market_type, allow_market_fallback):
        component_url = build_component_url(card_id, component_market_type, price_type)
        component_js = fetch_text(
            session,
            component_url,
            retries=retries,
            backoff=backoff,
            request_delay=request_delay,
        )
        try:
            return card_id, parse_price_history(component_js), url, component_market_type
        except ValueError as error:
            parse_errors.append(f"{component_market_type}: {error}")
            continue
    raise ValueError("; ".join(parse_errors))


def detect_url_column(fieldnames: list[str]) -> str:
    for fieldname in fieldnames:
        if fieldname.lower() in {"url", "urls", "mtggoldfish_url", "mtggoldfish_urls"}:
            return fieldname
    for fieldname in fieldnames:
        if "url" in fieldname.lower():
            return fieldname
    raise ValueError("Could not detect URL column. Pass --url-column explicitly.")


def read_urls(input_csv: Path, url_column: str | None = None) -> list[str]:
    with input_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{input_csv} has no header row")

        column = url_column or detect_url_column(reader.fieldnames)
        urls = [row[column].strip() for row in reader if row.get(column, "").strip()]

    if not urls:
        raise ValueError(f"No URLs found in {input_csv}")
    return urls


def history_to_long_rows(
    source_url: str,
    market_type: str,
    card_id: str,
    history_rows: list[list[str]],
) -> list[dict[str, str]]:
    card_name = history_rows[0][1] if len(history_rows[0]) > 1 else card_id
    long_rows = []
    for date_value, price_value in history_rows[1:]:
        long_rows.append(
            {
                "card_id": card_id,
                "market_type": market_type,
                "date": date_value,
                "price": price_value,
                "card_name": card_name,
                "source_url": source_url,
            }
        )
    return long_rows


def normalize_long_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        rows,
        key=lambda row: (row["card_id"], row["market_type"], row["date"]),
    )


def write_long_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    rows = normalize_long_rows(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LONG_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def append_long_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    rows = normalize_long_rows(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LONG_FIELDNAMES)
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)
        handle.flush()


def write_error_csv(errors: list[dict[str, str]], output_path: Path) -> None:
    if not errors:
        return
    error_path = output_path.with_suffix(".errors.csv")
    with error_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ERROR_FIELDNAMES)
        writer.writeheader()
        writer.writerows(errors)


def append_error_csv(error: dict[str, str], output_path: Path) -> None:
    error_path = output_path.with_suffix(".errors.csv")
    needs_header = not error_path.exists() or error_path.stat().st_size == 0
    with error_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ERROR_FIELDNAMES)
        if needs_header:
            writer.writeheader()
        writer.writerow(error)
        handle.flush()


def checkpointed_urls(output_path: Path, *, include_errors: bool = True) -> set[str]:
    urls: set[str] = set()
    paths = [output_path]
    if include_errors:
        paths.append(output_path.with_suffix(".errors.csv"))
    for path in paths:
        if not path.exists() or path.stat().st_size == 0:
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if "source_url" not in (reader.fieldnames or []):
                continue
            urls.update(row["source_url"] for row in reader if row.get("source_url"))
    return urls


def read_price_history_frame(path: Path = DEFAULT_BATCH_OUTPUT) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df["price"] = pd.to_numeric(df["price"])
    return df.set_index(["card_id", "market_type", "date"]).sort_index()


def fetch_batch(
    urls: list[str],
    *,
    market_type: str | None,
    output_path: Path,
    concurrency: int,
    retries: int,
    backoff: float,
    request_delay: float,
    card_delay: float,
    fail_fast: bool,
    retry_errors: bool,
    progress_every: int,
) -> tuple[int, int, int]:
    started_at = time.perf_counter()
    checkpoint_started_at = time.perf_counter()
    checkpointed = checkpointed_urls(output_path, include_errors=not retry_errors)
    print(
        f"Loaded {len(checkpointed)} checkpointed URLs in "
        f"{format_duration(time.perf_counter() - checkpoint_started_at)}"
    )
    pending_urls = [url for url in dict.fromkeys(urls) if url not in checkpointed]
    skipped_count = len(dict.fromkeys(urls)) - len(pending_urls)
    row_count = 0
    error_count = 0

    if skipped_count:
        print(f"Skipping {skipped_count} URLs already present in checkpoint files")
    if not pending_urls:
        return len(urls), 0, 0

    urls = pending_urls
    concurrency = max(1, min(concurrency, len(urls)))

    def fetch_one(index: int, url: str, slot: int) -> tuple[int, str, str, str, list[dict[str, str]]]:
        if card_delay and concurrency > 1:
            time.sleep(card_delay * slot / concurrency)
        requested_type = market_type or price_type_from_url(url)
        with requests.Session() as session:
            card_id, history_rows, resolved_url, resolved_type = fetch_price_history(
                url,
                requested_type,
                session=session,
                retries=retries,
                backoff=backoff,
                request_delay=request_delay,
                allow_market_fallback=market_type is None,
            )
        rows = history_to_long_rows(resolved_url, resolved_type, card_id, history_rows)
        return index, resolved_url, resolved_type, card_id, rows

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        completed_count = 0

        for batch_start, batch_urls in enumerate(batched(urls, concurrency), start=0):
            futures = {
                executor.submit(fetch_one, batch_start * concurrency + slot + 1, url, slot): (
                    batch_start * concurrency + slot + 1,
                    url,
                )
                for slot, url in enumerate(batch_urls)
            }

            for future in as_completed(futures):
                index, url = futures[future]
                try:
                    _, _, effective_type, card_id, rows = future.result()
                    append_long_csv(rows, output_path)
                    row_count += len(rows)
                    completed_count += 1
                    print(f"[{index}/{len(urls)}] wrote {len(rows)} {effective_type} rows for {card_id}")
                except Exception as error:
                    append_error_csv({"source_url": url, "error": str(error)}, output_path)
                    error_count += 1
                    completed_count += 1
                    print(f"[{index}/{len(urls)}] error: {url} ({error})")
                    if fail_fast:
                        for pending in futures:
                            pending.cancel()
                        raise

                if progress_every and completed_count % progress_every == 0:
                    elapsed = time.perf_counter() - started_at
                    rate = completed_count / elapsed if elapsed else 0
                    remaining = len(urls) - completed_count
                    eta = remaining / rate if rate else 0
                    print(
                        f"Progress: {completed_count}/{len(urls)} cards, "
                        f"{rate:.2f} cards/s, elapsed {format_duration(elapsed)}, "
                        f"ETA {format_duration(eta)}"
                    )

    return len(urls), row_count, error_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch MTGGoldfish card price histories.")
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Single MTGGoldfish card URL. Defaults to the URL generated by data_scraper.py.",
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=None,
        help="CSV containing MTGGoldfish card URLs for batch download.",
    )
    parser.add_argument(
        "--from-pipeline",
        action="store_true",
        help="Use data_scraper.html_pipeline(...) to generate MTGGoldfish URLs.",
    )
    parser.add_argument(
        "--cards-path",
        type=Path,
        default=DEFAULT_CARDS_PATH,
        help=f"Cards JSON path for --from-pipeline. Defaults to {DEFAULT_CARDS_PATH}.",
    )
    parser.add_argument(
        "--url-column",
        default=None,
        help="Column containing URLs. Auto-detects a column named url by default.",
    )
    parser.add_argument(
        "--type",
        choices=["paper", "online"],
        default=None,
        help="Price history type. Defaults to the URL fragment, or paper.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV output path.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=1.0,
        help="Seconds to wait after each HTTP request. Defaults to 1.0.",
    )
    parser.add_argument(
        "--card-delay",
        type=float,
        default=1.0,
        help="Seconds to spread requests within each concurrency wave. Defaults to 1.0.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=7,
        help="Parallel card fetches in batch mode. Defaults to 3.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Fetch attempts per request. Defaults to 3.",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=2.0,
        help="Retry backoff multiplier in seconds. Defaults to 2.0.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop batch mode on the first failed URL.",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Retry URLs in the .errors.csv checkpoint instead of treating them as completed.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print aggregate timing after this many completed cards. Set to 0 to disable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N input URLs. Useful for timing smoke tests.",
    )
    args = parser.parse_args()

    if args.input_csv or args.from_pipeline:
        output = args.output or DEFAULT_BATCH_OUTPUT
        if args.from_pipeline:
            from data_scraper import html_pipeline

            urls_started_at = time.perf_counter()
            urls = html_pipeline(str(args.cards_path))
            print(f"Generated {len(urls)} URLs in {format_duration(time.perf_counter() - urls_started_at)}")
        else:
            urls_started_at = time.perf_counter()
            urls = read_urls(args.input_csv, args.url_column)
            print(f"Read {len(urls)} URLs in {format_duration(time.perf_counter() - urls_started_at)}")
        if args.limit is not None:
            urls = urls[: args.limit]
            print(f"Limited input to {len(urls)} URLs")
        card_count, row_count, error_count = fetch_batch(
            urls,
            market_type=args.type,
            output_path=output,
            concurrency=args.concurrency,
            retries=args.retries,
            backoff=args.backoff,
            request_delay=args.request_delay,
            card_delay=args.card_delay,
            fail_fast=args.fail_fast,
            retry_errors=args.retry_errors,
            progress_every=args.progress_every,
        )
        print(f"Appended {row_count} rows from {card_count - error_count}/{card_count} cards to {output}")
        if error_count:
            print(f"Appended {error_count} errors to {output.with_suffix('.errors.csv')}")
        return

    output = args.output or DEFAULT_OUTPUT
    url = args.url or DEFAULT_URL
    with requests.Session() as session:
        card_id, rows, resolved_url, resolved_type = fetch_price_history(
            url,
            args.type,
            session=session,
            retries=args.retries,
            backoff=args.backoff,
            request_delay=args.request_delay,
            allow_market_fallback=args.type is None,
        )
    write_csv(rows, output)
    if resolved_url != url:
        print(f"Resolved {url} -> {resolved_url}")
    print(f"Wrote {len(rows) - 1} {resolved_type} rows for {card_id} to {output}")


if __name__ == "__main__":
    main()
