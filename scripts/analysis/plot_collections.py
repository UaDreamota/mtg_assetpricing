"""Render the catalog and collection figures, and export their underlying tables."""

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd

from scripts.analysis.explore_collections import COLORS, TYPES


TEAL, ORANGE, DARK, LIGHT = "#17685D", "#AB492B", "#263F3B", "#87ABA2"
HEAT = LinearSegmentedColormap.from_list("paper_teal", ["#F5F3EA", "#ACD0BD", "#338B78", "#125A50"])


def matrix_notes(ax, values, labels, vmax):
    ax.grid(False)
    ax.imshow(values, cmap=HEAT, vmin=0, vmax=vmax, aspect="auto")
    for (row, column), value in np.ndenumerate(values):
        ax.text(column, row, labels[row, column], ha="center", va="center",
                color="white" if value > .60 * vmax else DARK, fontsize=9)
    ax.tick_params(length=0)


def types_by_color(census, folder):
    table = pd.DataFrame(census["type_color"])
    counts = table.pivot(index="type", columns="color", values="cards").loc[list(TYPES), list(COLORS)]
    shares = table.pivot(index="type", columns="color", values="share_of_color").loc[list(TYPES), list(COLORS)]
    fig, ax = plt.subplots(figsize=(10.7, 4.8), layout="constrained")
    matrix_notes(ax, shares.to_numpy(), np.array([[f"{n:,}" for n in row] for row in counts.to_numpy()]), .75)
    sizes = {row["color"]: row["cards"] for row in census["colors"]}
    ax.set_xticks(range(len(COLORS)), [f"{c}\n{sizes[c]:,}" for c in COLORS])
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", pad=6)
    ax.set_yticks(range(len(TYPES)), TYPES)
    ax.set_title("Card types by color", loc="left", pad=10)
    ax.set_xlabel("Cells count designs. Darker cells represent a larger share of that color.")
    fig.savefig(folder / "types-by-color.png")
    plt.close(fig)


def mana_by_color(census, folder):
    rows = census["colors"]
    y = np.arange(len(rows))
    fig, (avg, curve) = plt.subplots(1, 2, figsize=(10.7, 4.0), layout="constrained",
                                     gridspec_kw={"width_ratios": [1, 1.8]})
    means = [r["mean_mana_nonlands"] for r in rows]
    avg.barh(y, means, height=.52, color=TEAL)
    for i, mean in enumerate(means):
        avg.text(mean + .06, i, f"{mean:.2f}", va="center", fontsize=9)
    avg.set_yticks(y, COLORS)
    avg.set_ylim(len(rows) - .5, -.5)
    avg.set_xlim(0, max(means) + .55)
    avg.set_xlabel("Mean mana value")
    avg.set_title("Mana value of nonlands", loc="left")
    proportions = np.array([np.array(r["mana_bins_0_to_7plus"]) / r["nonlands"] for r in rows])
    labels = np.array([["<1%" if 0 < v < .005 else f"{v:.0%}" for v in group] for group in proportions])
    matrix_notes(curve, proportions, labels, .35)
    curve.set_yticks(y, labels=[])
    curve.set_xticks(range(8), ["0", "1", "2", "3", "4", "5", "6", "7+"])
    curve.set_title("How the mana curve differs by color", loc="left")
    curve.set_xlabel("Card mana value · percentages within each color")
    fig.savefig(folder / "mana-by-color.png")
    plt.close(fig)


def text_and_keywords(census, folder):
    rows = [r for r in census["types"] if r["cards"]]
    fig, axes = plt.subplots(1, 2, figsize=(10.7, 4.1), layout="constrained", sharey=True)
    for ax, metric, title, color in zip(axes, ["mean_words", "mean_keywords"],
            ["Mean rules-text words", "Mean Scryfall keyword tags"], [TEAL, ORANGE]):
        values = [r[metric] for r in rows]
        ax.barh(range(len(rows)), values, color=color, height=.52)
        ax.axvline(census["all"][metric], color=DARK, linewidth=1.1, linestyle="--")
        for i, value in enumerate(values):
            ax.text(value + max(values) * .015, i, f"{value:.2f}" if metric == "mean_keywords" else f"{value:.1f}",
                    va="center", fontsize=9)
        ax.set_xlim(0, max(values) * 1.16)
        ax.set_title(title, loc="left")
        ax.set_xlabel("Dashed line is the whole-catalog mean")
    axes[0].set_yticks(range(len(rows)), [r["type"] for r in rows])
    axes[0].invert_yaxis()
    fig.savefig(folder / "rules-and-keywords.png")
    plt.close(fig)


def expansion_values(expansions, folder):
    sets = pd.DataFrame(expansions["sets"]).dropna(subset="one_copy_usd").copy()
    sets["date"] = pd.to_datetime(sets.release)
    good = sets.loc[sets.coverage.ge(.90)]
    fig, (timeline, chase) = plt.subplots(1, 2, figsize=(10.7, 3.8), layout="constrained",
                                         gridspec_kw={"width_ratios": [1.35, 1]})
    other = sets.loc[sets.coverage.lt(.90)]
    timeline.scatter(other.date, other.one_copy_usd, marker="o", facecolors="none", edgecolors="#838A85",
                     s=27, label="Under 90% coverage")
    timeline.scatter(good.date, good.one_copy_usd, color=TEAL, s=24, label="At least 90% coverage")
    if len(good):
        timeline.axhline(good.one_copy_usd.mean(), color=TEAL, linestyle="--", linewidth=1,
                         label="Mean at ≥90% coverage")
    timeline.set_ylim(bottom=0)
    timeline.set_title("One copy of each quoted set card", loc="left")
    timeline.set_ylabel("Basket quote (USD)")
    timeline.set_xlabel("Expansion release year")
    timeline.legend(frameon=False, loc="upper right", fontsize=8)
    leaders = good.nlargest(5, "top_five_share").iloc[::-1]
    chase.barh(leaders.expansion, leaders.top_five_share, height=.55, color=ORANGE)
    for i, share in enumerate(leaders.top_five_share):
        chase.text(share - .012, i, f"{share:.0%}", ha="right", va="center", color="white", fontsize=9)
    chase.set_xlim(0, 1)
    chase.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    chase.set_title("Five cards' share of the total", loc="left")
    chase.set_xlabel("Most concentrated sets with ≥90% coverage")
    fig.savefig(folder / "expansion-baskets.png")
    plt.close(fig)


def top_holdings(market, folder):
    curve = pd.DataFrame(market["rank_curve"])
    leaders = pd.DataFrame(market["holdings"]).head(6).iloc[::-1]
    fig, (share, prices) = plt.subplots(1, 2, figsize=(10.7, 3.6), layout="constrained")
    share.plot(curve["rank"], curve.share, color=TEAL, linewidth=2.3, label="One copy per name")
    share.plot(curve["rank"], curve["rank"] / market["eligible"], color="#809890", linestyle="--",
               linewidth=1.4, label="Equal dollars per name")
    share.scatter([300], [market["top_share"]], color=ORANGE, zorder=4)
    share.annotate(f'Top 300 · {market["top_share"]:.1%}', (300, market["top_share"]),
                   xytext=(-9, 9), textcoords="offset points", ha="right", fontsize=9)
    share.set_xscale("log")
    ticks = [1, 10, 100, 300, market["eligible"]]
    share.set_xticks(ticks, [f"{v:,}" for v in ticks])
    share.minorticks_off()
    share.set_ylim(0, 1.05)
    share.yaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    share.set_title("Where the over-$1 basket's value sits", loc="left")
    share.set_xlabel("Number of highest-priced names (log scale)")
    share.set_ylabel("Cumulative share of basket")
    share.legend(frameon=False, loc="lower right", fontsize=8)
    prices.barh(leaders.card_name, leaders.price, color=TEAL, height=.56)
    for i, price in enumerate(leaders.price):
        prices.text(price - leaders.price.max() * .025, i, f"${price:,.0f}", ha="right", va="center", color="white", fontsize=9)
    prices.set_title("Six largest one-copy holdings", loc="left")
    prices.set_xlabel("Cheapest qualifying quote per name (USD)")
    prices.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))
    fig.savefig(folder / "top-300-concentration.png")
    plt.close(fig)


def word_band_prices(records, folder):
    bands = pd.DataFrame(records)
    labels = bands.words.drop_duplicates().tolist()
    fig, (price, counts) = plt.subplots(2, 1, figsize=(10.7, 4.5), layout="constrained",
                                       sharex=True, gridspec_kw={"height_ratios": [3.1, 1]})
    for split, offset, color, marker, label in [
        ("zero", -.10, TEAL, "o", "Zero Standard exposure"),
        ("positive", .10, ORANGE, "D", "Positive Standard exposure"),
    ]:
        group = bands.loc[bands.standard.eq(split)].set_index("words").reindex(labels)
        x = np.arange(len(labels)) + offset
        price.errorbar(x, group["median"], yerr=[group["median"] - group.q1, group.q3 - group["median"]],
                       color=color, fmt=marker, capsize=3, linewidth=1.5, markersize=5, label=label)
        row = 1 if split == "zero" else 0
        for i, count in enumerate(group.n.fillna(0)):
            counts.text(i, row, f"{int(count):,}", va="center", ha="center", color=color, fontsize=9)
    price.set_yscale("log")
    ticks = [v for v in (.1, .2, .5, 1, 2, 5, 10, 20, 50, 100) if price.get_ylim()[0] <= v <= price.get_ylim()[1]]
    price.set_yticks(ticks, [f"${v:g}" for v in ticks])
    price.minorticks_off()
    price.set_ylabel("Paper quote (log scale)")
    price.set_title("Median quote and middle half, by rules-text length", loc="left")
    price.legend(frameon=False, loc="upper left", fontsize=9)
    counts.set_xticks(range(len(labels)), labels)
    counts.set_yticks([0, 1], ["Positive · names", "Zero · names"])
    counts.set_ylim(-.6, 1.6)
    counts.set_xlim(-.5, len(labels) - .5)
    counts.grid(False)
    counts.set_xlabel("Words per card")
    fig.savefig(folder / "price-by-word-count.png")
    plt.close(fig)


def export_results(summary, out):
    census = summary["collections"]["census"]
    pd.DataFrame(census["type_color"]).to_csv(out / "type-color-counts.csv", index=False)
    profiles = pd.DataFrame(census["colors"])
    bins = pd.DataFrame(profiles.pop("mana_bins_0_to_7plus").tolist(),
                        columns=["nonlands_mana_" + str(value) for value in (0, 1, 2, 3, 4, 5, 6, "7_plus")])
    leaders = profiles.pop("common_keywords").map(lambda tags: tags[0] if tags else {})
    profiles["most_frequent_tag"] = leaders.str.get("keyword")
    profiles["designs_with_top_tag"] = leaders.str.get("cards")
    profiles = pd.concat([profiles, bins], axis=1)
    profiles.to_csv(out / "color-profiles.csv", index=False, float_format="%.8g")
    for name, records in [
        ("prices-by-word-count", summary["word_price_bands"]),
        ("expansion-baskets", summary["collections"]["expansions"]["sets"]),
        ("top-300-holdings", summary["collections"]["market"]["holdings"]),
    ]:
        pd.DataFrame(records).to_csv(out / (name + ".csv"), index=False, float_format="%.10g")
    terms = [dict(model=name, **term, n=fit["n"], r2=fit["r2"])
             for name, fit in summary["regressions"].items() for term in fit["slopes"]]
    pd.DataFrame(terms).to_csv(out / "regression-coefficients.csv", index=False, float_format="%.10g")


def render(summary, figures, results):
    data = summary["collections"]
    types_by_color(data["census"], figures)
    mana_by_color(data["census"], figures)
    text_and_keywords(data["census"], figures)
    expansion_values(data["expansions"], figures)
    top_holdings(data["market"], figures)
    word_band_prices(summary["word_price_bands"], figures)
    export_results(summary, results)
