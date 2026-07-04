import argparse
import codecs
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import pandas as pd
import requests


BASE_DIR = Path(__file__).resolve().parent.parent
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


class FetchError(RuntimeError):
    pass


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
                raise FetchError(
                    f"Failed to fetch {url} "
                    f"(http_status={response.status_code}, reason={response.reason}, body={snippet!r})"
                )
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


def fetch_price_history(
    url: str,
    market_type: str | None = None,
    *,
    session: requests.Session | None = None,
    retries: int = 3,
    backoff: float = 2.0,
    request_delay: float = 1.0,
) -> tuple[str, list[list[str]]]:
    session = session or requests.Session()
    market_type = market_type or price_type_from_url(url)
    page_html = fetch_text(
        session,
        url,
        retries=retries,
        backoff=backoff,
        request_delay=request_delay,
    )
    card_id, price_type = extract_card_id(page_html)
    component_url = build_component_url(card_id, market_type, price_type)
    component_js = fetch_text(
        session,
        component_url,
        retries=retries,
        backoff=backoff,
        request_delay=request_delay,
    )
    return card_id, parse_price_history(component_js)


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


def checkpointed_urls(output_path: Path) -> set[str]:
    urls: set[str] = set()
    for path in [output_path, output_path.with_suffix(".errors.csv")]:
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
) -> tuple[int, int, int]:
    checkpointed = checkpointed_urls(output_path)
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

    def fetch_one(index: int, url: str) -> tuple[int, str, str, str, list[dict[str, str]]]:
        if card_delay:
            time.sleep(card_delay * ((index - 1) // concurrency))
        effective_type = market_type or price_type_from_url(url)
        with requests.Session() as session:
            card_id, history_rows = fetch_price_history(
                url,
                effective_type,
                session=session,
                retries=retries,
                backoff=backoff,
                request_delay=request_delay,
            )
        rows = history_to_long_rows(url, effective_type, card_id, history_rows)
        return index, url, effective_type, card_id, rows

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(fetch_one, index, url): (index, url)
            for index, url in enumerate(urls, start=1)
        }

        for future in as_completed(futures):
            index, url = futures[future]
            try:
                _, _, effective_type, card_id, rows = future.result()
                append_long_csv(rows, output_path)
                row_count += len(rows)
                print(f"[{index}/{len(urls)}] wrote {len(rows)} {effective_type} rows for {card_id}")
            except Exception as error:
                append_error_csv({"source_url": url, "error": str(error)}, output_path)
                error_count += 1
                print(f"[{index}/{len(urls)}] error: {url} ({error})")
                if fail_fast:
                    for pending in futures:
                        pending.cancel()
                    raise

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
        default=2.0,
        help="Seconds to wait after each HTTP request. Defaults to 1.0.",
    )
    parser.add_argument(
        "--card-delay",
        type=float,
        default=2.0,
        help="Seconds to stagger each concurrency wave in batch mode. Defaults to 2.0.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
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
    args = parser.parse_args()

    if args.input_csv or args.from_pipeline:
        output = args.output or DEFAULT_BATCH_OUTPUT
        if args.from_pipeline:
            from data_scraper import html_pipeline

            urls = html_pipeline(str(args.cards_path))
        else:
            urls = read_urls(args.input_csv, args.url_column)
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
        )
        print(f"Appended {row_count} rows from {card_count - error_count}/{card_count} cards to {output}")
        if error_count:
            print(f"Appended {error_count} errors to {output.with_suffix('.errors.csv')}")
        return

    output = args.output or DEFAULT_OUTPUT
    url = args.url or DEFAULT_URL
    with requests.Session() as session:
        card_id, rows = fetch_price_history(
            url,
            args.type,
            session=session,
            retries=args.retries,
            backoff=args.backoff,
            request_delay=args.request_delay,
        )
    write_csv(rows, output)
    print(f"Wrote {len(rows) - 1} rows for {card_id} to {output}")


if __name__ == "__main__":
    main()
