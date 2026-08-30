#AI built

import argparse
import sys
from pathlib import Path

import duckdb


BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


DATA_DIR = BASE_DIR / "data"
CARDS_JSON = DATA_DIR / "default-cards-20260614090813.json"
PRICES_PARQUET = DATA_DIR / "price_histories.parquet"
EXPOSURE_PARQUET = DATA_DIR / "all_f_exposure.parquet"
MTG_BANS_PARQUET = DATA_DIR / "mtgbans_timeline.parquet"
DEFAULT_OUTPUT = DATA_DIR / "final_card_panel.parquet"


def sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a monthly card panel from MTGTop8 exposure, MTGGoldfish prices, "
            "Scryfall card metadata, and MTGBans changes without loading the large "
            "price parquet into pandas memory."
        )
    )
    parser.add_argument("--cards-json", type=Path, default=CARDS_JSON)
    parser.add_argument("--prices-parquet", type=Path, default=PRICES_PARQUET)
    parser.add_argument("--exposure-parquet", type=Path, default=EXPOSURE_PARQUET)
    parser.add_argument("--bans-parquet", type=Path, default=MTG_BANS_PARQUET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--market-type",
        default="paper",
        choices=["paper", "online", "all"],
        help="Price market to keep from the MTGGoldfish price history.",
    )
    parser.add_argument(
        "--memory-limit",
        default="4GB",
        help="DuckDB memory limit. Temporary spill files are allowed if needed.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="Optional DuckDB thread count.",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=10,
        help="Rows to print after writing the output. Use 0 to skip.",
    )
    parser.add_argument(
        "--sparse-exposure-panel",
        action="store_true",
        help=(
            "Keep only card/months present in the exposure file. By default the "
            "script writes a dense card x month panel over the exposure card universe."
        ),
    )
    return parser.parse_args()


def build_final_panel(args: argparse.Namespace) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute(f"SET temp_directory = '{sql_path(DATA_DIR / 'duckdb_tmp')}'")
    if args.threads is not None:
        con.execute(f"SET threads = {args.threads}")

    market_filter = ""
    if args.market_type != "all":
        market_filter = f"AND market_type = '{args.market_type}'"

    exposure_columns = [
        "st_meta_exposure",
        "st_performance_exposure",
        "vi_meta_exposure",
        "vi_performance_exposure",
        "mo_meta_exposure",
        "mo_performance_exposure",
        "le_meta_exposure",
        "le_performance_exposure",
        "cedh_meta_exposure",
        "cedh_performance_exposure",
        "pau_meta_exposure",
        "pau_performance_exposure",
    ]
    if args.sparse_exposure_panel:
        panel_cte = """
        panel AS (
            SELECT *
            FROM exposure_sparse
        ),
        """
        exposure_select = "".join(f"            p.{col},\n" for col in exposure_columns)
        exposure_join = ""
    else:
        panel_cte = """
        months AS (
            SELECT month_start::DATE AS month_start
            FROM exposure_bounds,
            generate_series(min_month, max_month, INTERVAL '1 month') AS t(month_start)
        ),
        panel AS (
            SELECT
                date_diff('month', DATE '1970-01-01', m.month_start) AS month_ordinal,
                m.month_start,
                c.card_name
            FROM months AS m
            CROSS JOIN exposure_cards AS c
        ),
        """
        exposure_select = "".join(f"            coalesce(x.{col}, 0.0) AS {col},\n" for col in exposure_columns)
        exposure_join = """
        LEFT JOIN exposure_sparse AS x
            ON p.card_name = x.card_name
            AND p.month_start = x.month_start
        """

    query = f"""
    COPY (
        WITH exposure_sparse AS (
            SELECT
                CAST(month AS BIGINT) AS month_ordinal,
                CAST(DATE '1970-01-01' + CAST(month AS BIGINT) * INTERVAL '1 month' AS DATE)
                    AS month_start,
                *
                EXCLUDE (month)
            FROM read_parquet('{sql_path(args.exposure_parquet)}')
        ),
        exposure_bounds AS (
            SELECT min(month_start) AS min_month, max(month_start) AS max_month
            FROM exposure_sparse
        ),
        exposure_cards AS (
            SELECT DISTINCT card_name
            FROM exposure_sparse
        ),
        {panel_cte}
        price_printing_months AS (
            SELECT
                p.card_name,
                p.card_id,
                date_trunc('month', p.date)::DATE AS month_start,
                arg_max(p.price, p.date) AS printing_month_close
            FROM read_parquet('{sql_path(args.prices_parquet)}') AS p
            JOIN exposure_cards AS c
                ON p.card_name = c.card_name
            CROSS JOIN exposure_bounds AS b
            WHERE p.price IS NOT NULL
                {market_filter}
                AND p.date >= b.min_month
                AND p.date < b.max_month + INTERVAL '1 month'
            GROUP BY p.card_name, p.card_id, date_trunc('month', p.date)
        ),
        prices_monthly AS (
            SELECT
                card_name,
                month_start,
                median(printing_month_close) AS price_median_close,
                avg(printing_month_close) AS price_mean_close,
                min(printing_month_close) AS price_min_close,
                max(printing_month_close) AS price_max_close,
                count(*) AS price_printing_count
            FROM price_printing_months
            GROUP BY card_name, month_start
        ),
        prices_with_returns AS (
            SELECT
                *,
                price_median_close
                    / nullif(
                        lag(price_median_close)
                            OVER (PARTITION BY card_name ORDER BY month_start),
                        0
                    )
                    - 1 AS price_median_return
            FROM prices_monthly
        ),
        cards_raw AS (
            SELECT
                id,
                oracle_id,
                name AS card_name,
                CAST(released_at AS DATE) AS released_at,
                "set" AS set_code,
                set_name,
                set_type,
                collector_number,
                CAST(digital AS BOOLEAN) AS digital,
                CAST(foil AS BOOLEAN) AS foil,
                CAST(promo AS BOOLEAN) AS promo,
                CAST(reserved AS BOOLEAN) AS reserved,
                cmc,
                mana_cost,
                type_line,
                oracle_text,
                power,
                toughness
            FROM read_json_auto(
                '{sql_path(args.cards_json)}',
                maximum_object_size = 16777216
            )
            WHERE coalesce(CAST(digital AS BOOLEAN), false) = false
        ),
        card_metadata AS (
            SELECT
                card_name,
                min(released_at) AS first_released_at,
                arg_min(id, released_at) AS first_scryfall_id,
                arg_min(oracle_id, released_at) AS oracle_id,
                arg_min(set_code, released_at) AS first_set_code,
                arg_min(set_name, released_at) AS first_set_name,
                arg_min(set_type, released_at) AS first_set_type,
                arg_min(collector_number, released_at) AS first_collector_number,
                bool_or(coalesce(foil, false)) AS ever_foil,
                bool_or(coalesce(promo, false)) AS ever_promo,
                bool_or(coalesce(reserved, false)) AS reserved,
                max(cmc) AS cmc,
                arg_min(mana_cost, released_at) AS mana_cost,
                arg_min(type_line, released_at) AS type_line,
                arg_min(oracle_text, released_at) AS oracle_text,
                arg_min(power, released_at) AS power,
                arg_min(toughness, released_at) AS toughness
            FROM cards_raw
            GROUP BY card_name
        ),
        ban_events AS (
            SELECT
                card_name,
                date_trunc('month', coalesce(effective_date, date_announced))::DATE AS month_start,
                count(*) AS ban_change_count,
                sum(CASE WHEN change_type LIKE '%ban%' AND change_type NOT LIKE '%unban%' THEN 1 ELSE 0 END)
                    AS ban_count,
                sum(CASE WHEN change_type LIKE '%unban%' THEN 1 ELSE 0 END)
                    AS unban_count,
                sum(CASE WHEN change_type LIKE '%restrict%' THEN 1 ELSE 0 END)
                    AS restrict_count,
                string_agg(DISTINCT "format", ', ' ORDER BY "format") AS ban_formats,
                string_agg(DISTINCT change_type, ', ' ORDER BY change_type) AS ban_change_types
            FROM read_parquet('{sql_path(args.bans_parquet)}')
            WHERE card_name IS NOT NULL
                AND coalesce(effective_date, date_announced) IS NOT NULL
            GROUP BY card_name, date_trunc('month', coalesce(effective_date, date_announced))
        )
        SELECT
            p.month_ordinal,
            p.month_start,
            p.card_name,
{exposure_select}
            pr.price_median_close,
            pr.price_mean_close,
            pr.price_min_close,
            pr.price_max_close,
            pr.price_printing_count,
            pr.price_median_return,
            m.first_released_at,
            m.first_scryfall_id,
            m.oracle_id,
            m.first_set_code,
            m.first_set_name,
            m.first_set_type,
            m.first_collector_number,
            m.ever_foil,
            m.ever_promo,
            m.reserved,
            m.cmc,
            m.mana_cost,
            m.type_line,
            m.oracle_text,
            m.power,
            m.toughness,
            coalesce(b.ban_change_count, 0) AS ban_change_count,
            coalesce(b.ban_count, 0) AS ban_count,
            coalesce(b.unban_count, 0) AS unban_count,
            coalesce(b.restrict_count, 0) AS restrict_count,
            b.ban_formats,
            b.ban_change_types
        FROM panel AS p
{exposure_join}
        LEFT JOIN prices_with_returns AS pr
            ON p.card_name = pr.card_name
            AND p.month_start = pr.month_start
        LEFT JOIN card_metadata AS m
            ON p.card_name = m.card_name
        LEFT JOIN ban_events AS b
            ON p.card_name = b.card_name
            AND p.month_start = b.month_start
        ORDER BY p.month_start, p.card_name
    ) TO '{sql_path(args.output)}' (FORMAT PARQUET)
    """

    con.execute(query)

    row_count = con.execute(
        f"SELECT count(*) FROM read_parquet('{sql_path(args.output)}')"
    ).fetchone()[0]
    print(f"Wrote {row_count:,} rows to {args.output}")

    if args.preview:
        preview = con.execute(
            f"""
            SELECT month_start, card_name, price_median_close, price_median_return,
                   st_meta_exposure, mo_meta_exposure, ban_change_count
            FROM read_parquet('{sql_path(args.output)}')
            ORDER BY month_start DESC, card_name
            LIMIT {args.preview}
            """
        ).df()
        print(preview)
    
def main() -> None:
    build_final_panel(parse_args())


if __name__ == "__main__":
    main()
