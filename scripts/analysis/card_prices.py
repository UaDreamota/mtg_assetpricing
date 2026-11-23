"""Compare December paper quotes within creatures and within noncreatures."""
import argparse
import json
from datetime import date
from pathlib import Path
from itertools import combinations

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import t
from threadpoolctl import threadpool_limits

from scripts.analysis.explore_collections import register_printings, register_weekly_marks
from scripts.analysis.pre_regression import (
    create_lists_for_types, mana_cost_handling, regression_features, text_and_keyword_features,
)
from scripts.analysis.final_dataman import DATA_DIR

DATE = date(2025, 12, 1)
END = date(2025, 12, 31)
EXAMPLES = {
    "Colossal Dreadmaw", "Questing Beast", "Monastery Swiftspear", "Siege Rhino",
    "Snapcaster Mage", "Llanowar Elves", "Serra Angel", "Wall of Omens", "Opt",
    "Supreme Verdict", "Mox Opal", "Rest in Peace", "Flooded Strand", "Liliana of the Veil",
}
KEYWORDS = ("flying", "vigilance", "haste", "trample", "deathtouch", "lifelink",
            "flash", "prowess", "defender", "reach", "first_strike", "double_strike",
            "menace", "hexproof", "indestructible", "ward")
ROLES = ("card_draw", "mana_producer", "token_producer", "removal")
STATS = ["cmc", "power_num", "toughness_num", "colored_pip_slots", "text10", "Legendary"]
COLORS = ["is_" + color + "_spell" for color in ("red", "green", "blue", "black", "white")]
MECHANICS = STATS + ["keyword_" + k for k in KEYWORDS] + ["role_" + k for k in ROLES] + COLORS
BASE = ["uncommon", "rare", "mythic", "log_play", "stock", "issues"]


NONCREATURE_TYPES = ["instant", "artifact", "enchantment", "land", "planeswalker"]
NONCREATURE_ROLES = [*ROLES, "tutor", "counterspell", "board_wipe", "graveyard_interaction", "discard"]
NONCREATURE_MECHANICS = (
    ["cmc", "colored_pip_slots", "text10", "Legendary", "starting_loyalty"]
    + ["type_" + kind for kind in NONCREATURE_TYPES]
    + ["role_" + role for role in NONCREATURE_ROLES] + COLORS
)


def functional_role_features(df):
    """Count rules words and text roles after taking out the reminders."""
    result = df.copy()
    text = result["oracle_text"].fillna("").str.replace(r"\([^()]*\)", "", regex=True).str.lower()
    patterns = {
        "card_draw": r"\bdraws? (?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|\d+) cards?\b",
        "removal": r"\b(?:destroy|exile) (?:another |up to \w+ )?target\b|\bdeals? .{0,35}damage to (?:any target|target (?:creature|planeswalker))",
        "board_wipe": r"\b(?:destroy|exile) all (?:\w+ )?(?:creatures|permanents)\b|\bdamage to each creature\b",
        "counterspell": r"\bcounter target (?:\w+ )?spell\b",
        "tutor": r"\bsearch your library\b",
        "mana_producer": r"\badd (?:\{[^}]+\}|\w+ mana)|\badds? an additional\b.{0,20}\bmana\b",
        "graveyard_interaction": r"\bgraveyards?\b",
        "token_producer": r"\bcreate\b[^.\n]*\btokens?\b",
        "discard": r"\b(?:target (?:player|opponent)|each opponent) discards?\b",
    }
    for role, pattern in patterns.items():
        result[f"role_{role}"] = text.str.contains(pattern, regex=True)
    result["oracle_word_count_without_reminder"] = text.str.findall(r"\b[\w’'-]+\b").str.len()
    return result


def card_quotes(con, data_dir):
    sizes = pd.read_csv(DATA_DIR / "expansion_sizes.csv", parse_dates=["release_date"])
    con.register("sizes", sizes)
    cards = con.sql("""
        SELECT DISTINCT p.code, p.market_key AS card_name, p.rarity,
               s.center_m, s.release_date
        FROM printings p JOIN sizes s USING (code)
        WHERE p.lang='en' AND p.booster AND NOT p.oversized AND NOT p.promo
          AND p.rarity IN ('common','uncommon','rare','mythic')
          AND p.printed_type NOT LIKE '%Basic Land%' AND p.released <= s.release_date
    """).df()
    # How many slots can a name of this rarity take in each expansion?
    pool = cards.groupby(["code", "rarity"]).card_name.transform("size")
    slots = {"common": 10, "uncommon": 3, "rare": .875, "mythic": .125}
    cards["share"] = cards.rarity.map(slots) / 15 / pool
    cards["copies"] = cards.share * cards.center_m * 1_000_000
    issues = (cards.sort_values("share", ascending=False)
              .groupby(["code", "card_name"], as_index=False)
              .agg(rarity=("rarity", "first"), copies=("copies", "sum"), release=("release_date", "first")))
    stock = issues.groupby("card_name").agg(permanent=("copies", "sum"), issues=("code", "size"))
    marks = con.sql("""
        SELECT card AS card_name, regular_set AS code, min(price) AS price FROM marks
        WHERE regular_set IS NOT NULL GROUP BY card, regular_set
    """).df()
    quotes = (issues.merge(marks, on=["code", "card_name"], validate="one_to_one")
              .sort_values(["release", "code"]).drop_duplicates("card_name"))
    play = standard_play(con, data_dir / "mtgtop8")
    quotes = quotes.join(stock, on="card_name").join(play, on="card_name")
    quotes["play"] = quotes.play.fillna(0)
    return quotes


def standard_play(con, folder):
    # A missing decklist tells me nothing about which cards were in it.
    con.execute("""
        CREATE TEMP TABLE lists AS
        SELECT DISTINCT deck_id, market_name(regexp_replace(card_name, '^A-', '')) AS card_name
        FROM read_parquet(?) WHERE quantity>0 AND card_name IS NOT NULL
    """, [str(folder / "mtgtop8_cards/*.parquet")])
    con.execute("""
        CREATE TEMP TABLE decks AS
        SELECT deck_id FROM read_parquet(?) SEMI JOIN lists USING (deck_id)
        GROUP BY deck_id HAVING min(event_date)=max(event_date)
          AND date_trunc('month', min(event_date))=?
    """, [str(folder / "mtgtop8_decks/*.parquet"), DATE])
    n = con.sql("SELECT count(*) FROM decks").fetchone()[0]
    if n < 50:
        raise ValueError(f"Only {n} Standard lists in December")
    counts = con.sql("""
        SELECT card_name, count(*) AS n FROM decks JOIN lists USING (deck_id) GROUP BY card_name
    """).df()
    return counts.set_index("card_name").n.div(n).rename("play")


def card_features(con):
    cards = con.sql("""
        SELECT market_key AS card_name, max(mana_value) AS cmc,
               arg_min(type_line, released ORDER BY id) AS type_line,
               arg_min(oracle_text, released ORDER BY id) AS oracle_text,
               arg_min(keywords, released ORDER BY id) AS keywords,
               arg_min(colors, released ORDER BY id) AS colors,
               arg_min(color_identity, released ORDER BY id) AS color_identity,
               arg_min(mana_cost, released ORDER BY id) AS mana_cost,
               arg_min(power, released ORDER BY id) AS power,
               arg_min(toughness, released ORDER BY id) AS toughness,
               arg_min(loyalty, released ORDER BY id) AS loyalty,
               arg_min(defense, released ORDER BY id) AS defense
        FROM printings GROUP BY market_key
    """).df()
    cards = create_lists_for_types(cards)
    cards = regression_features(cards)
    cards = mana_cost_handling(cards)
    cards = text_and_keyword_features(cards)
    return functional_role_features(cards)


def rules_sample(cards, group, mechanics):
    if group == "creature":
        rows = cards.loc[cards.Creature.eq(1)].copy()
    else:
        kinds = ["Sorcery", "Instant", "Artifact", "Enchantment", "Land", "Planeswalker"]
        rows = cards.loc[cards.Creature.eq(0) & cards[kinds].sum(axis=1).eq(1)
                         & ~cards.type_line.str.contains("//", regex=False)
                         & (cards.Planeswalker.eq(0) | cards.loyalty_num.notna())].copy()
        for kind in NONCREATURE_TYPES:
            rows["type_" + kind] = rows[kind.title()]
        rows["starting_loyalty"] = rows.loyalty_num.fillna(0)
    rows["text10"] = rows.oracle_word_count_without_reminder / 10
    rows = rows.dropna(subset=mechanics).copy()
    for rarity in BASE[:3]:
        rows[rarity] = rows.rarity.eq(rarity)
    rows["log_play"] = np.log1p(100 * rows.play)
    rows["stock"] = np.log2(rows.permanent)
    rows["issue_count"] = rows.issues
    rows["issues"] = np.log2(rows.issue_count)
    return rows.sort_values("card_name").reset_index(drop=True)



def clustered_fit(x, y, groups):
    beta, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank != x.shape[1]:
        raise ValueError("The model has unidentified coefficients")
    clusters = len(np.unique(groups))
    scores = np.zeros((clusters, x.shape[1]))
    np.add.at(scores, groups, x * (y - x @ beta)[:, None])
    influence = scores @ np.linalg.inv(x.T @ x)
    correction = clusters / (clusters - 1) * (len(y) - 1) / (len(y) - x.shape[1])
    return beta, correction * (influence.T @ influence)


def type_study(rows, design, y, groups):
    # Use the creature face's own type line. An Adventure/transform back face and
    # abilities that grant extra types do not change these printed memberships.
    front = rows.type_line.str.split(" // ", n=1, regex=False).str[0]
    words = front.str.partition(" — ")
    is_creature = words[0].str.split().map(lambda parts: "Creature" in parts)
    if not is_creature.all():
        raise ValueError("Found a noncreature front in the creature sample")
    subtypes = words[2].str.replace("Time Lord", "Time_Lord", regex=False).str.get_dummies(sep=" ")
    subtypes = subtypes.rename(columns={"Time_Lord": "Time Lord"}).astype(bool)
    count = subtypes.sum()
    sets = subtypes.groupby(rows.code).any().sum()
    flags = subtypes.loc[:, (count >= 30) & (sets >= 5)].copy()
    flags["Artifact creature"] = words[0].str.split().map(lambda parts: "Artifact" in parts)
    x = np.column_stack([design, flags])
    beta, cov = clustered_fit(x, y, groups)
    df = len(np.unique(groups)) - 1
    critical = t.ppf(.975, df)

    def contrast(b, variance):
        se = float(np.sqrt(variance))
        return dict(b=b, se=se, low=b-critical*se, high=b+critical*se,
                    p=float(2*t.sf(abs(b/se), df)))

    terms = {}
    for j, label in enumerate(flags, start=design.shape[1]):
        yes = flags[label]
        terms[label] = dict(**contrast(beta[j], cov[j, j]), n=int(yes.sum()),
                            sets=int(rows.loc[yes, "code"].nunique()),
                            geo=float(np.exp(y[yes].mean())))
    pairs = []
    for left, right in combinations(("Dinosaur", "Merfolk", "Human"), 2):
        i, j = [design.shape[1] + flags.columns.get_loc(label) for label in (left, right)]
        overlap = set(rows.code[flags[left]]) & set(rows.code[flags[right]])
        pairs.append(dict(left=left, right=right, sharedSets=len(overlap),
                          **contrast(beta[i]-beta[j], cov[i,i]+cov[j,j]-2*cov[i,j])))
    ordered = sorted(pairs, key=lambda r: r["p"])
    adjusted = 0.0
    for i, r in enumerate(ordered):
        adjusted = max(adjusted, min(1., (len(ordered)-i)*r["p"]))
        r["holm"] = adjusted
    return dict(terms=terms, pairs=pairs, df=df, n=len(rows), counts=count.astype(int).to_dict())


def fit_prices(cards, group, mechanics):
    rows = rules_sample(cards, group, mechanics)
    # All folds contain members of each retained expansion.
    rows = rows.loc[rows.groupby("code").code.transform("size") >= 5].reset_index(drop=True)
    groups = pd.Categorical(rows.code).codes
    set_intercepts = pd.get_dummies(rows.code, prefix="set").astype(float)
    design = pd.concat([set_intercepts, rows[BASE + mechanics]], axis=1).astype(float)
    if group == "noncreature":
        # Center spell-only and planeswalker-only traits inside their eligible
        # subsets so type intercepts compare a 3-mana, 1-pip spell / 3 loyalty walker.
        for key, reference in [("cmc", 3), ("colored_pip_slots", 1)]:
            design[key] = (rows[key] - reference).where(rows.type_land.eq(0), 0)
        design["starting_loyalty"] = (rows.starting_loyalty - 3).where(rows.type_planeswalker.eq(1), 0)
    x = design.to_numpy()
    y = np.log(rows.price.to_numpy())
    beta, cov = clustered_fit(x, y, groups)
    clusters = len(set_intercepts.columns)
    se = np.sqrt(np.diag(cov))
    margin = t.ppf(.975, clusters - 1) * se

    folds = np.zeros(len(y), dtype=int)
    rng = np.random.default_rng(71)
    for code in np.unique(groups):
        indices = rng.permutation(np.flatnonzero(groups == code))
        folds[indices] = np.arange(len(indices)) % 5
    baseline = x[:, :clusters + len(BASE)]
    scores = {}
    held_out = {}
    for label, matrix in [("controls", baseline), ("withRules", x)]:
        predictions = np.empty(len(y))
        for fold in range(5):
            test = folds == fold
            b = np.linalg.lstsq(matrix[~test], y[~test], rcond=None)[0]
            predictions[test] = matrix[test] @ b
        held_out[label] = predictions
        scores[label] = 1 - ((y - predictions) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    terms = {}
    for key in BASE + mechanics:
        i = design.columns.get_loc(key)
        centered = x[:, i] - x[:, i].mean()
        raw = (centered @ (y - y.mean())) / (centered @ centered)
        if group == "noncreature":
            if key in ("cmc", "colored_pip_slots", "starting_loyalty"):
                subset = rows.type_planeswalker.eq(1) if key == "starting_loyalty" else rows.type_land.eq(0)
                v = rows.loc[subset, key].to_numpy()
                z = v - v.mean()
                raw = (z @ y[subset]) / (z @ z)
            elif key.startswith("type_"):
                sorcery = rows[["type_" + k for k in NONCREATURE_TYPES]].sum(axis=1).eq(0)
                raw = y[rows[key].eq(1)].mean() - y[sorcery].mean()
        if key in ("uncommon", "rare", "mythic"):
            raw = y[rows.rarity.eq(key)].mean() - y[rows.rarity.eq("common")].mean()
        terms[key] = dict(b=beta[i], raw=raw, low=beta[i]-margin[i], high=beta[i]+margin[i],
                          se=se[i], p=float(2*t.sf(abs(beta[i]/se[i]), clusters-1)), present=int(rows[key].gt(0).sum()))
    examples = {}
    for name in sorted(EXAMPLES.intersection(rows.card_name)):
        row = rows.loc[rows.card_name.eq(name)].iloc[0]
        examples[name] = dict(model=group, price=row.price, fitted=float(np.exp(held_out["withRules"][row.name])),
                           rarity=row.rarity, play=row.play, stock=row.permanent, issues=int(row.issue_count),
                           features={key:float(row[key]) for key in mechanics})
    print(group, len(rows), clusters, scores)
    result = dict(date=DATE.isoformat(), n=len(rows), sets=clusters, scores=scores, terms=terms)
    if group == "creature":
        result["typeStudy"] = type_study(rows, design, y, groups)
    else:
        result["sorceries"] = int(sorcery.sum())
    return result, examples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Local source archives")
    parser.add_argument("--output", type=Path, default=DATA_DIR.parent / "results/card_table/prices.json")
    args = parser.parse_args()
    with duckdb.connect(config={"threads": 3, "memory_limit": "2GB"}) as con:
        register_printings(con, args.data_dir / "default-cards-20260614090813.json", END)
        register_weekly_marks(con, args.data_dir / "price_histories.parquet", END)
        rows = card_quotes(con, args.data_dir).merge(card_features(con), on="card_name", validate="one_to_one")
    result, examples = fit_prices(rows, "creature", MECHANICS)
    result["noncreature"], others = fit_prices(rows, "noncreature", NONCREATURE_MECHANICS)
    result["cards"] = {**examples, **others}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    with threadpool_limits(limits=1):
        main()

