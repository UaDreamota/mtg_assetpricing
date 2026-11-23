"""Render the catalog and collection figures, and export their underlying tables."""

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FixedLocator, FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd

from scripts.analysis.explore_collections import COLORS, TYPES


CANVAS, INK, MUTED, GRID = "#121019", "#F8F3FA", "#C9BED3", "#362D40"
PINK, GREEN, CYAN, LAVENDER = "#FF55D4", "#74FFA8", "#63E5FF", "#C6ABFF"
TYPE_HEAT = LinearSegmentedColormap.from_list(
    "card_type", ["#241B2C", "#85366D", "#CB489F", PINK, "#FFB2E5"])
MANA_HEAT = LinearSegmentedColormap.from_list(
    "mana_curve", ["#172725", "#195548", "#289168", "#4AD993", GREEN])


def apply_plot_style():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
        "axes.labelcolor": MUTED, "axes.titlecolor": INK, "axes.titlesize": 13,
        "axes.titleweight": "bold", "axes.titlepad": 9, "axes.spines.top": False,
        "axes.spines.right": False, "axes.spines.left": False, "axes.edgecolor": GRID,
        "axes.grid": True, "axes.axisbelow": True, "grid.color": GRID, "grid.linewidth": .65,
        "axes.facecolor": CANVAS, "figure.facecolor": CANVAS, "savefig.facecolor": CANVAS,
        "xtick.color": MUTED, "ytick.color": MUTED, "ytick.major.size": 0, "savefig.dpi": 170,
    })


def matrix_notes(ax, values, labels, vmax, cmap):
    ax.grid(False)
    ax.imshow(values, cmap=cmap, vmin=0, vmax=vmax, aspect="auto")
    for (row, column), value in np.ndenumerate(values):
        rgb = np.array(cmap(value / vmax)[:3])
        linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
        luminance = linear @ [.2126, .7152, .0722]
        ax.text(column, row, labels[row, column], ha="center", va="center",
                color=CANVAS if luminance > .18 else INK, fontsize=9.5)
    ax.tick_params(length=0)


def types_by_color(census, folder):
    table = pd.DataFrame(census["type_color"])
    counts = table.pivot(index="type", columns="color", values="cards").loc[list(TYPES), list(COLORS)]
    shares = table.pivot(index="type", columns="color", values="share_of_color").loc[list(TYPES), list(COLORS)]
    fig, ax = plt.subplots(figsize=(10.7, 4.8), layout="constrained")
    matrix_notes(ax, shares.to_numpy(), np.array([[f"{n:,}" for n in row] for row in counts.to_numpy()]), .75, TYPE_HEAT)
    sizes = {row["color"]: row["cards"] for row in census["colors"]}
    ax.set_xticks(range(len(COLORS)), [f"{c}\n{sizes[c]:,}" for c in COLORS])
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", pad=6)
    ax.set_yticks(range(len(TYPES)), TYPES)
    ax.set_title("Card types by color", loc="left", pad=10)
    ax.set_xlabel("Card designs · brighter pink means a larger share of the color column")
    fig.savefig(folder / "types-by-color.png")
    plt.close(fig)


def mana_by_color(census, folder):
    rows = census["colors"]
    y = np.arange(len(rows))
    fig, (avg, curve) = plt.subplots(1, 2, figsize=(10.7, 4.0), layout="constrained",
                                     gridspec_kw={"width_ratios": [1, 1.8]})
    means = [r["mean_mana_nonlands"] for r in rows]
    avg.barh(y, means, height=.52, color=PINK)
    for i, mean in enumerate(means):
        avg.text(mean + .06, i, f"{mean:.2f}", va="center", fontsize=9)
    avg.set_yticks(y, COLORS)
    avg.set_ylim(len(rows) - .5, -.5)
    avg.set_xlim(0, max(means) + .55)
    avg.set_xlabel("Mean mana value")
    avg.set_title("Mana value of nonlands", loc="left")
    proportions = np.array([np.array(r["mana_bins_0_to_7plus"]) / r["nonlands"] for r in rows])
    labels = np.array([["<1%" if 0 < v < .005 else f"{v:.0%}" for v in group] for group in proportions])
    matrix_notes(curve, proportions, labels, .35, MANA_HEAT)
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
            ["Mean rules-text words", "Mean Scryfall keyword tags"], [GREEN, PINK]):
        values = [r[metric] for r in rows]
        ax.barh(range(len(rows)), values, color=color, height=.52)
        ax.axvline(census["all"][metric], color=LAVENDER, linewidth=1.1, linestyle="--")
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


def text_by_era(census, folder):
    eras = census["eras"]
    x = np.arange(len(eras))
    fig, ax = plt.subplots(figsize=(10.7, 3.2), layout="constrained")
    for key, label, color, marker in [
        ("mean_words", "All rules words", GREEN, "o"),
        ("mean_words_no_parentheses", "With parenthetical text removed", PINK, "D"),
    ]:
        ax.plot(x, [e[key] for e in eras], marker=marker, markersize=5, linewidth=2, color=color, label=label)
    labels = [str(e["start"]) if e["start"] == e["end"] else f'{e["start"]}–{str(e["end"])[2:]}' for e in eras]
    ax.set_xticks(x, labels)
    ax.set_title("How much text does a card from each era carry?", loc="left")
    ax.set_ylabel("Mean words per design")
    ax.set_xlabel("First qualifying release · wording from the archived Oracle snapshot")
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    fig.savefig(folder / "rules-words-by-era.png")
    plt.close(fig)


def expansion_values(expansions, folder):
    sets = pd.DataFrame(expansions["sets"]).dropna(subset="one_copy_usd").copy()
    sets["date"] = pd.to_datetime(sets.release)
    good = sets.loc[sets.coverage.ge(.90)]
    fig, (timeline, chase) = plt.subplots(1, 2, figsize=(10.7, 3.8), layout="constrained",
                                         gridspec_kw={"width_ratios": [1.35, 1]})
    other = sets.loc[sets.coverage.lt(.90)]
    timeline.scatter(other.date, other.one_copy_usd, marker="o", facecolors="none", edgecolors=LAVENDER,
                     s=27, label="Under 90% coverage")
    timeline.scatter(good.date, good.one_copy_usd, color=GREEN, s=24, label="At least 90% coverage")
    if len(good):
        timeline.axhline(good.one_copy_usd.mean(), color=GREEN, linestyle="--", linewidth=1,
                         label="Mean at ≥90% coverage")
    timeline.set_ylim(bottom=0)
    timeline.set_title("One copy of each quoted set card", loc="left")
    timeline.set_ylabel("Basket quote (USD)")
    timeline.set_xlabel("Expansion release year")
    timeline.legend(frameon=False, loc="upper right", fontsize=8)
    leaders = good.nlargest(5, "top_five_share").iloc[::-1]
    chase.barh(leaders.expansion, leaders.top_five_share, height=.55, color=PINK)
    for i, share in enumerate(leaders.top_five_share):
        chase.text(share - .012, i, f"{share:.0%}", ha="right", va="center", color=CANVAS, fontsize=9, fontweight="bold")
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
    share.plot(curve["rank"], curve.share, color=GREEN, linewidth=2.3, label="One copy per name")
    share.plot(curve["rank"], curve["rank"] / market["eligible"], color=CYAN, linestyle="--",
               linewidth=1.4, label="Equal dollars per name")
    share.scatter([300], [market["top_share"]], color=PINK, zorder=4)
    share.annotate(f'Top 300 · {market["top_share"]:.1%}', (300, market["top_share"]),
                   xytext=(-9, 9), textcoords="offset points", ha="right", fontsize=9, color=PINK)
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
    prices.barh(leaders.card_name, leaders.price, color=GREEN, height=.56)
    for i, price in enumerate(leaders.price):
        prices.text(price - leaders.price.max() * .025, i, f"${price:,.0f}", ha="right", va="center", color=CANVAS, fontsize=9, fontweight="bold")
    prices.set_title("Six largest one-copy holdings", loc="left")
    prices.set_xlabel("Cheapest qualifying quote per name (USD)")
    prices.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}"))
    fig.savefig(folder / "top-300-concentration.png")
    plt.close(fig)


def quarterly_estimates(records, folder):
    fig, axes = plt.subplots(1, 2, figsize=(10.7, 3.8), layout="constrained")
    x = np.arange(len(records))
    for ax, term, title in zip(axes, ["creature", "long_text"], ["Creature vs. noncreature", "16+ vs. 0–15 words"]):
        for split, offset, color, marker, label in [
            ("zero", -.085, GREEN, "o", "Zero Standard exposure"),
            ("positive", .085, PINK, "D", "Positive Standard exposure"),
        ]:
            ratios, lows, highs = np.array([[r["effects"][term][split][k] for k in ("ratio", "low", "high")]
                                             for r in records]).T
            ax.errorbar(x + offset, ratios, yerr=[ratios - lows, highs - ratios], fmt=marker, markersize=5,
                        color=color, capsize=3, linewidth=1.4, label=label)
        ax.axhline(1, color=LAVENDER, linestyle="--", linewidth=1)
        ax.set_xticks(x, [r["month"] for r in records])
        ax.set_yscale("log")
        low, high = ax.get_ylim()
        ticks = [v for v in (.25, .5, .75, .9, 1, 1.1, 1.25, 1.5, 2, 3, 5, 10, 20, 50)
                 if low <= v <= high]
        if len(ticks) > 7:
            ticks = [v for v in ticks if v not in (.9, 1.1, 1.25)]
        ax.yaxis.set_major_locator(FixedLocator(ticks))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v-1:+.0%}" if v != 1 else "0%"))
        ax.minorticks_off()
        ax.set_title(title, loc="left")
    axes[0].set_ylabel("Adjusted quote difference · 95% interval")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside lower center", ncol=2, frameon=False)
    fig.savefig(folder / "quarterly-price-check.png")
    plt.close(fig)


def word_band_prices(records, folder):
    bands = pd.DataFrame(records)
    labels = bands.words.drop_duplicates().tolist()
    fig, (price, counts) = plt.subplots(2, 1, figsize=(10.7, 4.5), layout="constrained",
                                       sharex=True, gridspec_kw={"height_ratios": [3.1, 1]})
    for split, offset, color, marker, label in [
        ("zero", -.10, GREEN, "o", "Zero Standard exposure"),
        ("positive", .10, PINK, "D", "Positive Standard exposure"),
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
        ("type-profiles", census["types"]), ("design-eras", census["eras"]),
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
    text_by_era(data["census"], figures)
    expansion_values(data["expansions"], figures)
    top_holdings(data["market"], figures)
    quarterly_estimates(summary["quarter_checks"], figures)
    word_band_prices(summary["word_price_bands"], figures)
    export_results(summary, results)
