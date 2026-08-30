import argparse
import json
import re
from pathlib import Path

import pandas as pd
import requests


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
ANNOUNCEMENTS_URL = "https://www.mtgbans.info/announcements"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)


def fetch_html(url: str, timeout: float, user_agent: str) -> str:
    response = requests.get(url, headers={"User-Agent": user_agent}, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_js_array(html: str, variable_name: str) -> str:
    marker = f"{variable_name}:["
    start = html.index(marker) + len(f"{variable_name}:")
    level = 0
    in_string = False
    escaped = False

    for index, char in enumerate(html[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "[":
            level += 1
        elif char == "]":
            level -= 1
            if level == 0:
                return html[start : index + 1]

    raise ValueError(f"Could not find complete {variable_name} array in MTGBans HTML")


def parse_announcements(html: str) -> list[dict]:
    js_array = extract_js_array(html, "announcements")
    json_text = re.sub(r'([{,])([A-Za-z_][A-Za-z0-9_]*):', r'\1"\2":', js_array)
    return json.loads(json_text)


def normalize_change_type(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def flatten_announcements(announcements: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for announcement in announcements:
        sources = announcement.get("sources", [])
        source_titles = " | ".join(source.get("title", "") for source in sources)
        source_urls = " | ".join(source.get("uri", "") for source in sources)

        for changeset in announcement.get("changesets", []):
            for change in changeset.get("changes", []):
                change_type = change.get("type")
                for card in change.get("cards", []):
                    rows.append(
                        {
                            "announcement_id": announcement.get("id"),
                            "date_announced": announcement.get("dateAnnounced"),
                            "effective_date": announcement.get("dateEffective"),
                            "summary": announcement.get("summary"),
                            "format": changeset.get("format"),
                            "change_type": normalize_change_type(change_type or ""),
                            "change_type_raw": change_type,
                            "card_name": card.get("name"),
                            "scryfall_id": card.get("scryfallId"),
                            "scryfall_uri": card.get("scryfallUri"),
                            "classification": card.get("classification"),
                            "source_titles": source_titles,
                            "source_urls": source_urls,
                        }
                    )

    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("No ban/restriction rows parsed from MTGBans announcements")

    df["date_announced"] = pd.to_datetime(df["date_announced"], errors="coerce")
    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce")
    return df.sort_values(["effective_date", "announcement_id", "format", "change_type", "card_name"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape MTGBans.info's banned/restricted announcement timeline."
    )
    parser.add_argument("--url", default=ANNOUNCEMENTS_URL, help="MTGBans announcements URL.")
    parser.add_argument(
        "--html-path",
        type=Path,
        default=None,
        help="Optional local MTGBans HTML file to parse instead of fetching.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=DATA_DIR / "mtgbans_timeline.csv",
        help="CSV output path.",
    )
    parser.add_argument(
        "--output-parquet",
        type=Path,
        default=DATA_DIR / "mtgbans_timeline.parquet",
        help="Parquet output path.",
    )
    parser.add_argument(
        "--raw-html-output",
        type=Path,
        default=None,
        help="Optional path to save fetched raw HTML for reproducibility.",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent header.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.html_path is not None:
        html = args.html_path.read_text(encoding="utf-8")
    else:
        html = fetch_html(args.url, timeout=args.timeout, user_agent=args.user_agent)
        if args.raw_html_output is not None:
            args.raw_html_output.parent.mkdir(parents=True, exist_ok=True)
            args.raw_html_output.write_text(html, encoding="utf-8")

    announcements = parse_announcements(html)
    df = flatten_announcements(announcements)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output_csv, index=False)

    args.output_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.output_parquet, index=False)

    print(
        f"Wrote {len(df)} ban/restriction rows from {len(announcements)} announcements "
        f"to {args.output_csv} and {args.output_parquet}."
    )


if __name__ == "__main__":
    main()
