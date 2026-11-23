"""Describe paper card designs and value one-copy baskets at a single month-end."""

import numpy as np
import pandas as pd


COLORS = ("White", "Blue", "Black", "Red", "Green", "Multicolor", "Colorless")
MONO = dict(zip("WUBRG", COLORS[:5]))
TYPES = ("Creature", "Instant", "Sorcery", "Enchantment", "Artifact", "Land", "Planeswalker", "Battle")


def color_group(values):
    if len(values) == 1:
        return MONO[values[0]]
    return "Multicolor" if len(values) else "Colorless"


def register_printings(con, path, end):
    # These are the market-name aliases used in the project's portfolio inputs.
    con.execute("""
        CREATE TEMP MACRO market_name(raw_name) AS CASE raw_name
            WHEN 'Ratonhnhaké꞉ton' THEN 'Ratonhnhaké:ton'
            WHEN 'With Great Power . . .' THEN 'With Great Power...'
            WHEN 'Last Voyage of the _____' THEN 'Last Voyage of the ________'
            WHEN 'Wolf in _____ Clothing' THEN 'Wolf in ________ Clothing'
            ELSE replace(raw_name, ' // ', ' / ') END
    """)
    con.execute("""
        CREATE TEMP TABLE printings AS SELECT
            id, oracle_id, name, lang, layout, released_at::DATE AS released,
            "set" AS code, set_name, set_type, rarity, oversized, promo, booster,
            border_color, security_stamp,
            market_name(CASE WHEN layout='split' THEN name
                             ELSE coalesce(card_faces[1].name, name) END) AS market_key,
            type_line AS printed_type, color_identity,
            coalesce(nullif(card_faces[1].mana_cost, ''), nullif(mana_cost, '')) AS mana_cost,
            coalesce(card_faces[1].power, power) AS power,
            coalesce(card_faces[1].toughness, toughness) AS toughness,
            coalesce(card_faces[1].loyalty, loyalty) AS loyalty,
            coalesce(card_faces[1].defense, defense) AS defense,
            coalesce(card_faces[1].type_line, type_line) AS type_line,
            coalesce(card_faces[1].colors, colors) AS colors, cmc AS mana_value,
            coalesce(card_faces[1].oracle_text, oracle_text, '') AS oracle_text, keywords
        FROM read_json_auto(?, maximum_object_size=16777216)
        WHERE list_contains(games, 'paper') AND NOT digital AND released_at::DATE <= ?
          AND layout NOT IN ('art_series', 'token', 'double_faced_token', 'emblem')
    """, [str(path), end])


def read_designs(con):
    from scripts.analysis.pre_regression import create_lists_for_types, text_and_keyword_features

    designs = con.sql("""
        SELECT oracle_id, name AS card_name, released, type_line, colors, mana_value, oracle_text, keywords
        FROM printings WHERE lang='en' AND NOT oversized AND oracle_id IS NOT NULL
          AND set_type NOT IN ('memorabilia', 'minigame', 'token')
          AND coalesce(security_stamp, '') <> 'acorn' AND coalesce(border_color, '') <> 'silver'
          AND type_line IS NOT NULL AND colors IS NOT NULL AND isfinite(mana_value)
        QUALIFY row_number() OVER (PARTITION BY oracle_id ORDER BY released, id)=1
        ORDER BY oracle_id
    """).df()
    designs = create_lists_for_types(designs)
    type_flags = designs.reindex(columns=TYPES, fill_value=0)
    designs[list(TYPES)] = type_flags
    designs = designs.loc[type_flags.any(axis=1)].copy()
    # The published text/token routine computes word counts and Scryfall keyword counts.
    designs = text_and_keyword_features(designs)
    designs["color"] = designs.colors.map(color_group)
    designs["words_no_parentheses"] = (
        designs.oracle_text.str.replace(r"\([^()]*\)", "", regex=True)
        .str.findall(r"\b[\w’'-]+\b").str.len()
    )
    return designs


def design_stats(rows):
    return {
        "cards": len(rows),
        "mean_words": float(rows.oracle_word_count.mean()) if len(rows) else None,
        "median_words": float(rows.oracle_word_count.median()) if len(rows) else None,
        "mean_keywords": float(rows.keyword_count.mean()) if len(rows) else None,
        "median_keywords": float(rows.keyword_count.median()) if len(rows) else None,
        "no_keyword": int(rows.keyword_count.eq(0).sum()),
        "no_rules_words": int(rows.oracle_word_count.eq(0).sum()),
        "mean_words_no_parentheses": float(rows.words_no_parentheses.mean()) if len(rows) else None,
    }


def describe_designs(cards):
    matrix, color_rows = [], []
    for color in COLORS:
        rows = cards.loc[cards.color.eq(color)]
        spells = rows.loc[rows.Land.eq(0)]
        mana_bins = spells.mana_value.clip(upper=7).apply(np.floor).astype(int).value_counts()
        keywords = rows.keywords.explode().dropna().value_counts()
        color_rows.append({
            "color": color, **design_stats(rows), "nonlands": len(spells),
            "mean_mana_nonlands": float(spells.mana_value.mean()),
            "median_mana_nonlands": float(spells.mana_value.median()),
            "mean_mana_with_lands": float(rows.mana_value.mean()),
            "mana_bins_0_to_7plus": [int(mana_bins.get(i, 0)) for i in range(8)],
            "common_keywords": [{"keyword": k, "cards": int(n)} for k, n in keywords.head(3).items()],
        })
        for kind in TYPES:
            count = int(rows[kind].sum())
            matrix.append({"type": kind, "color": color, "cards": count,
                           "share_of_color": count / len(rows), "color_total": len(rows)})

    years = cards.released.dt.year
    eras = []
    for start in range(1990, int(years.max()) + 1, 5):
        subset = cards.loc[years.between(start, start + 4)]
        if subset.empty:
            continue
        eras.append({"start": int(subset.released.dt.year.min()), "end": int(subset.released.dt.year.max()),
                     **design_stats(subset), "creature_mean_words": float(subset.loc[subset.Creature.eq(1), "oracle_word_count"].mean())})
    return {
        "all": design_stats(cards), "colors": color_rows, "type_color": matrix,
        "types": [{"type": kind, **design_stats(cards.loc[cards[kind].eq(1)])} for kind in TYPES],
        "eras": eras,
    }


def register_weekly_marks(con, path, end):
    # Identify plain Name [SET] quotes before normalizing the market name.
    con.execute(r"""
        CREATE TEMP TABLE marks AS
        WITH quotes AS (
            SELECT market_name(card_name) AS card, card_id, date, price,
                   regexp_extract(card_id, '\[([A-Za-z0-9]+)\]$', 1) AS suffix,
                   CASE WHEN card_id = card_name || ' [' || suffix || ']' THEN lower(suffix) END AS regular_set
            FROM read_parquet(?) WHERE market_type='paper' AND isfinite(price) AND price>0
              AND card_id IS NOT NULL AND card_name IS NOT NULL AND date BETWEEN ? AND ?
        ), daily AS (
            SELECT card, card_id, date, median(price) AS price, min(regular_set) AS regular_set
            FROM quotes GROUP BY card, card_id, date
        )
        SELECT card, card_id, min(regular_set) AS regular_set, median(price) AS price
        FROM daily GROUP BY card, card_id HAVING count(*) >= 3
    """, [str(path), end - pd.Timedelta(days=6), end])


def expansion_baskets(con):
    rows = con.sql("""
        WITH eligible AS (
            SELECT code, set_name, market_key AS card, min(released) AS issued
            FROM printings WHERE set_type='expansion' AND lang='en' AND booster AND NOT promo
              AND NOT oversized AND rarity IN ('common','uncommon','rare','mythic')
              AND NOT regexp_matches(type_line, '(^| )Basic (Snow )?Land')
            GROUP BY code, set_name, market_key
        ), sets AS (
            SELECT code, min(issued) AS first_release FROM eligible GROUP BY code
            HAVING min(issued) >= DATE '2010-01-01'
        ), regular AS (
            SELECT regular_set AS code, card, min(price) AS price FROM marks
            WHERE regular_set IS NOT NULL GROUP BY regular_set, card
        )
        SELECT e.code, e.set_name, s.first_release, e.card, r.price
        FROM eligible e JOIN sets s USING (code) LEFT JOIN regular r USING (code, card)
        ORDER BY e.code, r.price DESC NULLS LAST, e.card
    """).df()
    records = []
    for code, items in rows.groupby("code"):
        quoted = items.dropna(subset="price")
        n = len(quoted)
        total = float(quoted.price.sum()) if n else None
        top_five = float(quoted.price.head(5).sum()) if n else None
        records.append({
            "code": code, "expansion": items.set_name.iloc[0],
            "release": items.first_release.iloc[0].date().isoformat(),
            "eligible_names": len(items), "quoted_names": n, "coverage": n / len(items),
            "one_copy_usd": total, "mean_card_usd": total / n if n else None,
            "top_name": quoted.card.iloc[0] if n else None,
            "top_price_usd": float(quoted.price.iloc[0]) if n else None,
            "top_five_share": top_five / total if n else None,
            "value_ex_top_five_usd": total - top_five if n else None,
        })
    records.sort(key=lambda x: (x["release"], x["code"]))
    comparison = []
    for threshold in (.80, .90, .95, 1.0):
        included = [r for r in records if r["coverage"] >= threshold]
        comparison.append({
            "coverage_at_least": threshold, "sets": len(included),
            "mean_basket_usd": float(np.mean([r["one_copy_usd"] for r in included])) if included else None,
            "median_basket_usd": float(np.median([r["one_copy_usd"] for r in included])) if included else None,
        })
    return {"sets": records, "coverage_sensitivity": comparison}


def expensive_holdings(con):
    cheapest = con.sql("""
        SELECT card AS card_name, card_id AS printing, price
        FROM marks SEMI JOIN (SELECT DISTINCT market_key FROM printings) r ON card = r.market_key
        QUALIFY row_number() OVER (PARTITION BY card ORDER BY price, card_id)=1
        ORDER BY price DESC, card, card_id
    """).df()
    eligible = cheapest.loc[cheapest.price.gt(1)].copy()
    if len(eligible) < 300:
        raise ValueError(f"Only {len(eligible)} eligible names for the top-300 basket")
    top = eligible.head(300).copy()
    total, top_value = float(eligible.price.sum()), float(top.price.sum())
    top["rank"] = np.arange(1, len(top) + 1)
    top["weight_top_300"] = top.price / top_value
    top["weight_all_over_1"] = top.price / total
    top["cumulative_share_all_over_1"] = top.weight_all_over_1.cumsum()
    cumulative = eligible.price.cumsum() / total
    ranks = np.unique(np.r_[1, 5, 10, 100, 300, len(eligible),
                            np.geomspace(1, len(eligible), 100).round().astype(int)])
    return {
        "covered_names": len(cheapest), "exactly_one": int(cheapest.price.eq(1).sum()),
        "below_one": int(cheapest.price.lt(1).sum()), "eligible": len(eligible), "all_value": total,
        "top_value": top_value, "top_share": top_value / total, "top_effective_names": float(1 / top.weight_top_300.pow(2).sum()),
        "rank_curve": [{"rank": int(i), "share": float(cumulative.iloc[i - 1])} for i in ranks],
        "holdings": top[["rank", "card_name", "printing", "price", "weight_top_300", "weight_all_over_1",
                         "cumulative_share_all_over_1"]].to_dict("records"),
    }


def collect(registry, prices, month):
    import duckdb

    end = (pd.Timestamp(month) + pd.offsets.MonthEnd(0)).date()
    with duckdb.connect(config={"threads": 3, "memory_limit": "2GB"}) as con:
        register_printings(con, registry, end)
        census = describe_designs(read_designs(con))
        register_weekly_marks(con, prices, end)
        return {"census": census, "expansions": expansion_baskets(con), "market": expensive_holdings(con)}
