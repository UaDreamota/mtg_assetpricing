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
    ban_format_prefixes = {
        "Standard": "st",
        "Vintage": "vi",
        "Modern": "mo",
        "Legacy": "le",
        "Commander": "cedh",
        "Pauper": "pau",
    }
    if args.sparse_exposure_panel:
        panel_cte = """
        panel AS (
            SELECT *
            FROM exposure_sparse
        ),
        """
        exposure_select = "".join(f"            p.{col},\n" for col in exposure_columns)
        exposure_lag_select = "".join(
            f"            lag(p.{col}) OVER "
            f"(PARTITION BY p.card_name ORDER BY p.month_start) AS {col}_lag1,\n"
            for col in exposure_columns
        )
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
        exposure_lag_select = "".join(
            f"            lag(coalesce(x.{col}, 0.0)) OVER "
            f"(PARTITION BY p.card_name ORDER BY p.month_start) AS {col}_lag1,\n"
            for col in exposure_columns
        )
        exposure_join = """
        LEFT JOIN exposure_sparse AS x
            ON p.card_name = x.card_name
            AND p.month_start = x.month_start
        """

    ban_feature_aggregation = ",\n".join(
        (
            f"            max(CASE WHEN format = '{format_name}' "
            f"THEN banned_this_month ELSE 0 END)::BOOLEAN AS {prefix}_banned_this_month,\n"
            f"            max(CASE WHEN format = '{format_name}' "
            f"THEN coalesce(is_banned, 0) ELSE 0 END)::BOOLEAN AS {prefix}_is_banned,\n"
            f"            max(CASE WHEN format = '{format_name}' AND is_banned = 1 "
            f"THEN date_diff('month', last_ban_month, month_start) END) "
            f"AS {prefix}_months_since_ban"
        )
        for format_name, prefix in ban_format_prefixes.items()
    )
    ban_feature_select = "".join(
        (
            f"            coalesce(bf.{prefix}_banned_this_month, false) "
            f"AS {prefix}_banned_this_month,\n"
            f"            coalesce(bf.{prefix}_is_banned, false) AS {prefix}_is_banned,\n"
            f"            bf.{prefix}_months_since_ban,\n"
        )
        for prefix in ban_format_prefixes.values()
    )

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
        prices_with_lags AS (
            SELECT
                *,
                lag(month_start) OVER (
                    PARTITION BY card_name ORDER BY month_start
                ) AS previous_price_month,
                lag(price_median_close) OVER (
                    PARTITION BY card_name ORDER BY month_start
                ) AS previous_price_median_close
            FROM prices_monthly
        ),
        prices_with_returns AS (
            SELECT
                *,
                CASE
                    WHEN date_diff('month', previous_price_month, month_start) = 1
                    THEN price_median_close / nullif(previous_price_median_close, 0) - 1
                END AS price_median_return,
                CASE
                    WHEN date_diff('month', previous_price_month, month_start) = 1
                        AND price_median_close > 0
                        AND previous_price_median_close > 0
                    THEN ln(price_median_close) - ln(previous_price_median_close)
                END AS price_median_log_return
            FROM prices_with_lags
        ),
        cards_raw AS (
            SELECT
                id,
                oracle_id,
                coalesce(card_faces[1].name, name) AS card_name,
                CAST(released_at AS DATE) AS released_at,
                "set" AS set_code,
                set_name,
                set_type,
                rarity,
                collector_number,
                CAST(digital AS BOOLEAN) AS digital,
                CAST(foil AS BOOLEAN) AS foil,
                CAST(promo AS BOOLEAN) AS promo,
                CAST(reprint AS BOOLEAN) AS reprint,
                CAST(reserved AS BOOLEAN) AS reserved,
                cmc,
                coalesce(card_faces[1].colors, colors) AS colors,
                color_identity,
                keywords,
                coalesce(
                    nullif(card_faces[1].mana_cost, ''),
                    nullif(mana_cost, '')
                ) AS mana_cost,
                coalesce(card_faces[1].type_line, type_line) AS type_line,
                coalesce(card_faces[1].oracle_text, oracle_text) AS oracle_text,
                coalesce(card_faces[1].power, power) AS power,
                coalesce(card_faces[1].toughness, toughness) AS toughness,
                coalesce(card_faces[1].loyalty, loyalty) AS loyalty,
                coalesce(card_faces[1].defense, defense) AS defense
            FROM read_json_auto(
                '{sql_path(args.cards_json)}',
                maximum_object_size = 16777216
            )
            WHERE coalesce(CAST(digital AS BOOLEAN), false) = false
                AND layout NOT IN ('art_series', 'token', 'double_faced_token', 'emblem')
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
                arg_min(rarity, released_at) AS first_rarity,
                arg_min(collector_number, released_at) AS first_collector_number,
                bool_or(coalesce(foil, false)) AS ever_foil,
                bool_or(coalesce(promo, false)) AS ever_promo,
                bool_or(coalesce(reprint, false)) AS ever_reprinted,
                bool_or(coalesce(reserved, false)) AS reserved,
                count(DISTINCT id) AS paper_printing_count,
                count(DISTINCT set_code) AS paper_set_count,
                max(cmc) AS cmc,
                arg_min(colors, released_at) AS colors,
                arg_min(color_identity, released_at) AS color_identity,
                arg_min(keywords, released_at) AS keywords,
                arg_min(mana_cost, released_at) AS mana_cost,
                arg_min(type_line, released_at) AS type_line,
                arg_min(oracle_text, released_at) AS oracle_text,
                arg_min(power, released_at) AS power,
                arg_min(toughness, released_at) AS toughness,
                arg_min(loyalty, released_at) AS loyalty,
                arg_min(defense, released_at) AS defense
            FROM cards_raw
            GROUP BY card_name
        ),
        card_printing_months AS (
            SELECT
                card_name,
                date_trunc('month', released_at)::DATE AS month_start,
                count(DISTINCT id) AS printing_release_count,
                max(released_at) AS latest_release_date
            FROM cards_raw
            GROUP BY card_name, date_trunc('month', released_at)
        ),
        card_printing_initial AS (
            SELECT
                c.card_name,
                max(c.released_at) AS latest_release_date
            FROM cards_raw AS c
            CROSS JOIN exposure_bounds AS b
            WHERE c.released_at < b.min_month
            GROUP BY c.card_name
        ),
        ban_source AS (
            SELECT
                announcement_id,
                card_name,
                "format" AS format,
                change_type,
                coalesce(effective_date, date_announced) AS event_date,
                date_trunc(
                    'month', coalesce(effective_date, date_announced)
                )::DATE AS month_start
            FROM read_parquet('{sql_path(args.bans_parquet)}')
            WHERE card_name IS NOT NULL
                AND coalesce(effective_date, date_announced) IS NOT NULL
        ),
        ban_events AS (
            SELECT
                card_name,
                month_start,
                count(*) AS ban_change_count,
                sum(CASE WHEN change_type LIKE '%ban%' AND change_type NOT LIKE '%unban%' THEN 1 ELSE 0 END)
                    AS ban_count,
                sum(CASE WHEN change_type LIKE '%unban%' THEN 1 ELSE 0 END)
                    AS unban_count,
                sum(CASE WHEN change_type LIKE '%restrict%' THEN 1 ELSE 0 END)
                    AS restrict_count,
                string_agg(DISTINCT format, ', ' ORDER BY format) AS ban_formats,
                string_agg(DISTINCT change_type, ', ' ORDER BY change_type) AS ban_change_types
            FROM ban_source
            GROUP BY card_name, month_start
        ),
        ban_state_events AS (
            SELECT
                card_name,
                format,
                month_start,
                CASE change_type WHEN 'banned' THEN 1 ELSE 0 END AS state_update
            FROM ban_source
            WHERE change_type IN ('banned', 'unbanned')
                AND format IN ('Standard', 'Vintage', 'Modern', 'Legacy', 'Commander', 'Pauper')
            QUALIFY row_number() OVER (
                PARTITION BY card_name, format, month_start
                ORDER BY event_date DESC, announcement_id DESC
            ) = 1
        ),
        ban_event_flags AS (
            SELECT
                card_name,
                format,
                month_start,
                max(CASE WHEN change_type = 'banned' THEN 1 ELSE 0 END)
                    AS banned_this_month
            FROM ban_source
            WHERE change_type IN ('banned', 'unbanned')
                AND format IN ('Standard', 'Vintage', 'Modern', 'Legacy', 'Commander', 'Pauper')
            GROUP BY card_name, format, month_start
        ),
        ban_card_formats AS (
            SELECT DISTINCT s.card_name, s.format
            FROM ban_state_events AS s
            JOIN exposure_cards AS c USING (card_name)
        ),
        ban_state_bounds AS (
            SELECT
                least(min(s.month_start), b.min_month) AS min_month,
                b.max_month
            FROM ban_state_events AS s
            CROSS JOIN exposure_bounds AS b
            GROUP BY b.min_month, b.max_month
        ),
        ban_state_months AS (
            SELECT month_start::DATE AS month_start
            FROM ban_state_bounds,
            generate_series(min_month, max_month, INTERVAL '1 month') AS t(month_start)
        ),
        ban_state_grid AS (
            SELECT
                cf.card_name,
                cf.format,
                m.month_start,
                coalesce(f.banned_this_month, 0) AS banned_this_month,
                last_value(e.state_update IGNORE NULLS) OVER (
                    PARTITION BY cf.card_name, cf.format
                    ORDER BY m.month_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS is_banned,
                last_value(
                    CASE WHEN e.state_update = 1 THEN m.month_start END
                    IGNORE NULLS
                ) OVER (
                    PARTITION BY cf.card_name, cf.format
                    ORDER BY m.month_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS last_ban_month
            FROM ban_card_formats AS cf
            CROSS JOIN ban_state_months AS m
            LEFT JOIN ban_state_events AS e
                ON cf.card_name = e.card_name
                AND cf.format = e.format
                AND m.month_start = e.month_start
            LEFT JOIN ban_event_flags AS f
                ON cf.card_name = f.card_name
                AND cf.format = f.format
                AND m.month_start = f.month_start
        ),
        ban_features AS (
            SELECT
                card_name,
                month_start,
{ban_feature_aggregation}
            FROM ban_state_grid
            CROSS JOIN exposure_bounds AS b
            WHERE month_start BETWEEN b.min_month AND b.max_month
            GROUP BY card_name, month_start
        )
        SELECT
            p.month_ordinal,
            p.month_start,
            p.card_name,
{exposure_select}
{exposure_lag_select}
            pr.price_median_close,
            pr.price_mean_close,
            pr.price_min_close,
            pr.price_max_close,
            pr.price_printing_count,
            pr.price_median_return,
            pr.price_median_log_return,
            m.first_released_at,
            m.first_scryfall_id,
            m.oracle_id,
            m.first_set_code,
            m.first_set_name,
            m.first_set_type,
            m.first_rarity,
            m.first_collector_number,
            m.ever_foil,
            m.ever_promo,
            m.ever_reprinted,
            m.reserved,
            m.paper_printing_count,
            m.paper_set_count,
            coalesce(cpm.printing_release_count, 0) AS printing_release_count,
            (
                coalesce(cpm.printing_release_count, 0) > 0
                AND p.month_start > date_trunc('month', m.first_released_at)::DATE
            ) AS reprinted_this_month,
            coalesce(
                max(cpm.latest_release_date) OVER (
                    PARTITION BY p.card_name
                    ORDER BY p.month_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ),
                cpi.latest_release_date
            ) AS most_recent_printing_date,
            date_diff(
                'month',
                coalesce(
                    max(cpm.latest_release_date) OVER (
                        PARTITION BY p.card_name
                        ORDER BY p.month_start
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    cpi.latest_release_date
                ),
                p.month_start
            ) AS months_since_most_recent_printing,
            m.cmc,
            m.colors,
            m.color_identity,
            m.keywords,
            m.mana_cost,
            m.type_line,
            m.oracle_text,
            m.power,
            m.toughness,
            m.loyalty,
            m.defense,
{ban_feature_select}
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
        LEFT JOIN card_printing_months AS cpm
            ON p.card_name = cpm.card_name
            AND p.month_start = cpm.month_start
        LEFT JOIN card_printing_initial AS cpi
            ON p.card_name = cpi.card_name
        LEFT JOIN ban_events AS b
            ON p.card_name = b.card_name
            AND p.month_start = b.month_start
        LEFT JOIN ban_features AS bf
            ON p.card_name = bf.card_name
            AND p.month_start = bf.month_start
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
