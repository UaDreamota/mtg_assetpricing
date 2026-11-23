"""Describe paper card designs, value collections and fit monthly price equations."""

import argparse
from datetime import date
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd
from scipy.stats import t
from threadpoolctl import threadpool_limits


from scripts.analysis.plot_collections import CYAN, GREEN, MUTED, PINK, apply_plot_style


RARITIES = ("common", "uncommon", "rare", "mythic")
EXPOSURE = {"zero": "Zero Standard exposure", "positive": "Positive Standard exposure"}
COLORS = {"zero": GREEN, "positive": PINK, "all": CYAN}
MARKERS = {"zero": "o", "positive": "D", "all": "s"}


def load_month(panel, month):
    import duckdb
    from scripts.analysis.pre_regression import create_lists_for_types, text_and_keyword_features

    with duckdb.connect() as con:
        rows = con.execute("""
            SELECT card_name, type_line, oracle_text, keywords, cmc AS mana_value,
                   first_rarity AS rarity, first_set_code AS origin, reserved,
                   months_since_most_recent_printing AS since_printing,
                   st_meta_exposure AS exposure, price_median_close AS price
            FROM read_parquet(?) WHERE month_start = ?
              AND price_median_close > 0 AND isfinite(price_median_close)
              AND first_rarity IN ('common', 'uncommon', 'rare', 'mythic')
              AND type_line IS NOT NULL AND first_set_code IS NOT NULL
              AND isfinite(cmc) AND cmc >= 0 AND months_since_most_recent_printing >= 0
              AND isfinite(st_meta_exposure) AND st_meta_exposure >= 0 AND reserved IS NOT NULL
            ORDER BY card_name
        """, [str(panel), month]).df()
    if rows.empty or rows.card_name.duplicated().any() or not rows.exposure.gt(0).any():
        raise ValueError("Expected one row per quoted card name and positive Standard coverage")
    rows = text_and_keyword_features(create_lists_for_types(rows))
    return rows.assign(played=rows.exposure.gt(0), long_text=rows.oracle_word_count.gt(15))


def fit_model(rows, cutoff, specification="interaction"):
    """Fit the selected price equation, clustering uncertainty by first set."""
    x = pd.DataFrame(dict(
        creature=rows.Creature,
        long_text=rows.oracle_word_count.gt(cutoff).astype(float),
    ))
    if specification not in ("traits", "controls", "interaction"):
        raise ValueError(f"Unknown price equation: {specification}")
    if specification != "traits":
        rarity = pd.get_dummies(rows.rarity, dtype=float).reindex(columns=RARITIES[1:], fill_value=0)
        x = pd.concat([rarity, x], axis=1).assign(
            played=rows.played.astype(float), exposure=np.log1p(100 * rows.exposure),
            mana=rows.mana_value, last_printing=np.log1p(rows.since_printing),
            reserved=rows.reserved.astype(float),
        )
    if specification == "interaction":
        for term in ("creature", "long_text"):
            x[term + "_played"] = x[term] * x.played

    # Subtract grand means for the traits model, or first-set means for fixed effects.
    frame = x.assign(log_price=np.log(rows.price))
    within = frame - (frame.mean() if specification == "traits" else frame.groupby(rows.origin).transform("mean"))
    y = within.pop("log_price").to_numpy()
    a = within.to_numpy()
    beta, _, rank, _ = np.linalg.lstsq(a, y, rcond=None)
    group_ids, groups = pd.factorize(rows.origin)
    n, k = a.shape
    g = len(groups)
    intercepts = 1 if specification == "traits" else g
    if rank != k or g < 2 or n <= k + intercepts:
        raise ValueError("The selected month has insufficient support for the regression")

    residual = y - a @ beta
    scores = np.zeros((g, k))
    np.add.at(scores, group_ids, a * residual[:, None])
    influence = np.linalg.solve(a.T @ a, scores.T).T
    correction = (g / (g - 1)) * ((n - 1) / (n - intercepts - k))
    covariance = correction * (influence.T @ influence)
    critical = t.ppf(.975, g - 1)

    def contrast(*terms):
        weights = x.columns.isin(terms).astype(float)
        estimate = weights @ beta
        margin = critical * np.sqrt(weights @ covariance @ weights)
        return dict(zip(("ratio", "low", "high"), np.exp([estimate, estimate - margin, estimate + margin]).tolist()))

    effects = {}
    for term in ("creature", "long_text"):
        effects[term] = ({"zero": contrast(term), "positive": contrast(term, term + "_played")}
                         if specification == "interaction" else {"all": contrast(term)})
    if "mana" in x:
        effects["mana"] = {"all": contrast("mana")}
    se = np.sqrt(np.diag(covariance))
    coefficients = [{"term": term, "beta": float(b), "cluster_se": float(s),
                     "low": float(b - critical * s), "high": float(b + critical * s)}
                    for term, b, s in zip(x.columns, beta, se)]
    rss = residual @ residual
    total_ss = np.square(frame.log_price - frame.log_price.mean()).sum()
    return {"n": n, "sets": g, "r2": float(1 - rss / total_ss), "effects": effects, "slopes": coefficients}


def fit(rows, cutoff):
    return fit_model(rows, cutoff)["effects"]


def price_stats(prices):
    if prices.empty:
        raise ValueError("The selected month has an empty comparison group")
    q1, median, q3 = prices.quantile([.25, .5, .75]).tolist()
    return {"n": len(prices), "mean": float(prices.mean()), "q1": q1, "median": median, "q3": q3}


def prices_by_word_count(rows):
    labels = ["0–10", "11–15", "16–20", "21–30", "31–45", "46–60", "61+"]
    bands = pd.cut(rows.oracle_word_count, [-1, 10, 15, 20, 30, 45, 60, np.inf], labels=labels)
    records = []
    for (band, played), group in rows.groupby([bands, "played"], observed=True, sort=True):
        records.append({"words": str(band), "standard": "positive" if played else "zero",
                        **price_stats(group.price)})
    return records


def summarize(rows, month):
    groups = {
        "creature": {"Noncreature": rows.Creature.eq(0), "Creature": rows.Creature.eq(1)},
        "long_text": {"0–15 words": ~rows.long_text, "16+ words": rows.long_text},
    }
    splits = {"zero": ~rows.played, "positive": rows.played}
    prices = rows.price
    edges = np.geomspace(10.0 ** np.floor(np.log10(prices.min())),
                         10.0 ** np.ceil(np.log10(prices.max())), 41)
    counts, _ = np.histogram(prices, bins=edges)
    models = {spec: fit_model(rows, 15, spec) for spec in ("traits", "controls", "interaction")}
    estimates = {10: fit(rows, 10), 15: models["interaction"]["effects"], 20: fit(rows, 20)}
    without_lands = rows.loc[rows.Land.eq(0)]
    return {
        "month": month.isoformat()[:7],
        "sample": {**price_stats(prices), "first_sets": rows.origin.nunique(),
                   "under_one_dollar": int(prices.lt(1).sum()), "positive_exposure": int(rows.played.sum()),
                   "median_words": float(rows.oracle_word_count.median()), "over_15_words": int(rows.long_text.sum())},
        "histogram": {"edges": edges.tolist(), "counts": counts.tolist()},
        "word_price_bands": prices_by_word_count(rows),
        "raw_rarity": {rarity: price_stats(rows.loc[rows.rarity.eq(rarity), "price"]) for rarity in RARITIES},
        "raw_features": {
            feature: {label: {split: price_stats(rows.loc[mask & in_group, "price"])
                              for split, in_group in splits.items()}
                      for label, mask in categories.items()}
            for feature, categories in groups.items()
        },
        "adjusted": estimates[15],
        "regressions": models,
        "text_cutoffs": {str(cutoff): result["long_text"] for cutoff, result in estimates.items()},
        "excluding_lands": {"n": len(without_lands), "adjusted": fit(without_lands, 15)},
    }


def quarter_checks(panel, month, summary):
    import duckdb

    ends = pd.date_range(end=pd.Timestamp(month) - pd.Timedelta(days=1), periods=3, freq="QE-DEC")
    months = [d.to_period("M").start_time.date() for d in ends]
    with duckdb.connect() as con:
        covered = {row[0] for row in con.execute("""
            SELECT DISTINCT month_start FROM read_parquet(?)
            WHERE month_start BETWEEN ? AND ? AND st_meta_exposure>0 AND price_median_close>0
        """, [str(panel), months[0], month]).fetchall()}
    records = []
    for earlier in months:
        if earlier not in covered:
            continue
        rows = load_month(panel, earlier)
        records.append({"month": earlier.isoformat()[:7], "n": len(rows), "positive": int(rows.played.sum()),
                        "effects": fit(rows, 15)})
    records.append({"month": summary["month"], "n": summary["sample"]["n"],
                    "positive": summary["sample"]["positive_exposure"], "effects": summary["adjusted"]})
    return records


def currency(value, _):
    return f"${value:,.0f}" if value == int(value) else f"${value:.2f}"


def price_axis(ax, ticks):
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(FixedLocator(ticks))
    ax.xaxis.set_major_formatter(FuncFormatter(currency))
    ax.minorticks_off()
    ax.set_xlabel("Paper quote (USD, log scale)")


def mark(ax, position, middle, low, high, color, marker="o"):
    ax.hlines(position, low, high, color=color, linewidth=3)
    ax.plot(middle, position, marker=marker, color=color, markersize=7, zorder=3)


def exposure_legend(fig, groups):
    fig.legend(handles=[Line2D([], [], color=COLORS[k], marker=MARKERS[k], linestyle="none", label=v)
                        for k, v in groups.items()], loc="outside lower center", ncol=len(groups), frameon=False)


def distribution(summary, folder):
    fig, (hist, rarity) = plt.subplots(1, 2, figsize=(10.7, 3.5), layout="constrained")
    bins = summary["histogram"]
    fractions = np.array(bins["counts"]) / summary["sample"]["n"]
    hist.stairs(fractions, bins["edges"], color=COLORS["zero"], fill=True, alpha=.85)
    hist.axvline(1, color=PINK, linestyle="--", linewidth=1.2)
    under_dollar = summary["sample"]["under_one_dollar"] / summary["sample"]["n"]
    hist.text(1.15, fractions.max() * .81, f"{under_dollar:.0%} below $1", color=PINK)
    hist.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    hist.set_ylabel("Share of card names")
    hist.set_title("Distribution across card names", loc="left")
    price_axis(hist, [.1, 1, 10, 100, 1000])

    for i, level in enumerate(RARITIES):
        values = summary["raw_rarity"][level]
        mark(rarity, i, values["median"], values["q1"], values["q3"], PINK)
    rarity.set_yticks(range(len(RARITIES)), [level.title() for level in RARITIES])
    rarity.invert_yaxis()
    rarity.set_title("Median and middle half by first rarity", loc="left")
    price_axis(rarity, [.2, .5, 1, 2, 5])
    fig.savefig(folder / "paper-price-distribution.png")
    plt.close(fig)


def raw_comparisons(summary, folder):
    fig, axes = plt.subplots(1, 2, figsize=(10.7, 3.4), layout="constrained", sharex=True)
    for ax, (feature, title) in zip(axes, [("creature", "Card type"), ("long_text", "Rules-text length")]):
        categories = summary["raw_features"][feature]
        for i, values in enumerate(categories.values()):
            for offset, (group, stats) in zip((-.13, .13), values.items()):
                mark(ax, i + offset, stats["median"], stats["q1"], stats["q3"], COLORS[group], MARKERS[group])
        ax.set_yticks(range(len(categories)), categories.keys())
        ax.set_ylim(1.45, -.45)
        ax.set_title(title, loc="left")
        price_axis(ax, [.2, .4, .8, 1.6, 3.2])
    exposure_legend(fig, EXPOSURE)
    fig.savefig(folder / "creatures-and-rules-text.png")
    plt.close(fig)


def adjusted_comparisons(summary, folder):
    fig, ax = plt.subplots(figsize=(10.7, 3.6), layout="constrained")
    terms = [("creature", "Creature vs. noncreature"), ("long_text", "16+ vs. 0–15 words"),
             ("mana", "One additional mana")]
    for i, (term, _) in enumerate(terms):
        estimates = summary["adjusted"][term]
        offsets = [-.15, .15] if len(estimates) == 2 else [0]
        for offset, (group, values) in zip(offsets, estimates.items()):
            mark(ax, i + offset, values["ratio"], values["low"], values["high"], COLORS[group], MARKERS[group])
    ax.set_yticks(range(len(terms)), [label for _, label in terms])
    ax.set_ylim(2.45, -.45)
    ax.axvline(1, color=MUTED, linestyle="--", linewidth=1.2)
    ax.set_xscale("log")
    ax.xaxis.set_major_locator(FixedLocator([.75, 1, 1.5, 2, 3, 5]))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value - 1:+.0%}" if value != 1 else "0%"))
    ax.minorticks_off()
    ax.set_xlabel("Estimated difference in quoted price with 95% intervals")
    ax.set_title("Adjusted price comparisons", loc="left")
    exposure_legend(fig, {**EXPOSURE, "all": "Both groups"})
    fig.savefig(folder / "adjusted-price-gaps.png")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=Path("data/final_card_panel.parquet"))
    parser.add_argument("--registry", type=Path, default=Path("data/default-cards-20260614090813.json"))
    parser.add_argument("--prices", type=Path, default=Path("data/price_histories.parquet"))
    parser.add_argument("--month", default="2025-12", help="Panel month as YYYY-MM")
    parser.add_argument("--output", type=Path, default=Path.cwd(), help="Root containing figures and results")
    parser.add_argument("--replay", action="store_true", help="Regenerate figures and tables from the saved summary")
    args = parser.parse_args()

    summary_path = args.output / "results/exploration/summary.json"
    if args.replay:
        summary = json.loads(summary_path.read_text())
    else:
        from scripts.analysis.explore_collections import collect

        month = date.fromisoformat(args.month + "-01")
        with threadpool_limits(limits=1):
            summary = summarize(load_month(args.panel, month), month)
            summary["quarter_checks"] = quarter_checks(args.panel, month, summary)
            summary["collections"] = collect(args.registry, args.prices, month)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")

    folder = args.output / "figures/neon"
    folder.mkdir(parents=True, exist_ok=True)
    apply_plot_style()
    distribution(summary, folder)
    raw_comparisons(summary, folder)
    adjusted_comparisons(summary, folder)
    if "collections" in summary:
        from scripts.analysis.plot_collections import render

        render(summary, folder, summary_path.parent)

    # The card features use the separate December printing regressions.
    card_results = summary_path.parents[1] / "card_table/prices.json"
    if card_results.exists() and summary["month"] == "2025-12":
        from scripts.analysis.plot_card_features import render_card_features

        render_card_features(card_results, folder)


if __name__ == "__main__":
    main()
