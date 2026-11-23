"""Plot the December price fits and put their terms in ordinary percentage units."""

import csv
import json
from math import expm1, log
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter


PAPER = "#121019"
INK = "#F8F3FA"
MUTED = "#C4BCCD"
GRID = "#393343"
PINK = "#FF55D4"
MINT = "#74FFA8"

PANELS = (
    ("Card colors", (("White", "is_white_spell"), ("Blue", "is_blue_spell"),
                     ("Black", "is_black_spell"), ("Red", "is_red_spell"),
                     ("Green", "is_green_spell")), [-30, -20, -10, 0, 10]),
    ("Noncreature type relative to sorcery",
     (("Instant", "type_instant"), ("Artifact", "type_artifact"),
      ("Enchantment", "type_enchantment"), ("Land", "type_land"),
      ("Planeswalker", "type_planeswalker")), [-60, -30, 0, 30, 60, 90, 120]),
    ("Creature keywords",
     (("Flying", "keyword_flying"), ("Haste", "keyword_haste"),
      ("Trample", "keyword_trample"), ("Lifelink", "keyword_lifelink"),
      ("Deathtouch", "keyword_deathtouch")), [-15, -10, -5, 0, 5, 10]),
    ("Rules text and abilities",
     (("10 more words", "text10"), ("Draws cards", "role_card_draw"),
      ("Produces mana", "role_mana_producer"), ("Searches library", "role_tutor"),
      ("Clears the board", "role_board_wipe")), [-30, -15, 0, 15, 30, 45, 60]),
)


def comparison(term, group):
    if term in ("uncommon", "rare", "mythic"):
        return term.capitalize() + " vs common, quoted printing"
    if term.startswith("type_"):
        return term[5:].capitalize() + " vs sorcery; spells at 3 mana and 1 colored pip, planeswalkers at 3 loyalty in the adjusted fit"
    if term.startswith("is_"):
        return term.split("_")[1].capitalize() + " present vs absent; other color flags held fixed in the adjusted fit"
    if term.startswith("keyword_"):
        return "Listed keyword present vs absent"
    if term.startswith("role_"):
        return "Rules text matches this role vs no match"
    if term in ("cmc", "colored_pip_slots"):
        unit = "mana value" if term == "cmc" else "colored mana symbol"
        return "One additional " + unit + (" on a nonland" if group == "Noncreatures" else "")
    return {
        "log_play": "0% to 1% of downloaded Standard lists",
        "stock": "Double estimated cumulative expansion supply",
        "issues": "Double number of covered expansion issues",
        "text10": "Ten additional rules words after removing reminder text",
        "Legendary": "Legendary vs nonlegendary",
        "starting_loyalty": "One additional starting loyalty on a planeswalker",
        "power_num": "One additional point of power",
        "toughness_num": "One additional point of toughness",
    }[term]


def feature_rows(prices):
    rows = []
    for group, fit in (("Creatures", prices), ("Noncreatures", prices["noncreature"])):
        for term, stats in fit["terms"].items():
            step = log(2) if term == "log_play" else 1
            raw, adjusted, low, high = [100 * expm1(step * stats[key]) for key in ("raw", "b", "low", "high")]
            rows.append(dict(group=group, term=term, comparison=comparison(term, group),
                             raw_difference_pct=raw, adjusted_difference_pct=adjusted,
                             ci_low_pct=low, ci_high_pct=high, full_model_p_value=stats["p"]))
    return rows


def percent(value, _):
    if value == 0:
        return "0%"
    return f"{value:+.0f}%".replace("-", "−")


def render_card_features(prices_path, figures_dir):
    """Use pointwise expansion-clustered intervals. Feature p-values have no multiplicity correction."""
    prices_path = Path(prices_path)
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)
    prices = json.loads(prices_path.read_text())
    rows = feature_rows(prices)
    with prices_path.with_name("feature-effects.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    groups = {name: {r["term"]: r for r in rows if r["group"] == name}
              for name in ("Creatures", "Noncreatures")}
    with plt.rc_context({"font.family": "DejaVu Sans", "axes.grid": False,
                         "figure.facecolor": PAPER, "axes.facecolor": PAPER,
                         "text.color": INK, "axes.labelcolor": MUTED,
                         "xtick.color": MUTED, "ytick.color": INK}):
        fig, axes = plt.subplots(2, 2, figsize=(11.8, 6.8), layout="constrained")
        fig.get_layout_engine().set(w_pad=.12, h_pad=.12, wspace=.055, hspace=.035)
        fig.suptitle("How card features relate to December 2025 paper quotes",
                     fontsize=17, weight="bold", x=.01, ha="left", color=INK)
        styles = (("Creatures", PINK, "o"), ("Noncreatures", MINT, "D"))
        for ax, (title, items, ticks) in zip(axes.flat, PANELS):
            ax.set_title(title, fontsize=12, weight="bold", loc="left", color=INK, pad=9)
            ax.axvline(0, color="#B5ABB9", linewidth=1.2, zorder=1)
            for i, (_, key) in enumerate(items):
                shared = all(key in group for group in groups.values())
                for j, (name, color, marker) in enumerate(styles):
                    if key not in groups[name]:
                        continue
                    row = groups[name][key]
                    y = i + (j - .5) * .32 if shared else i
                    value = row["adjusted_difference_pct"]
                    ax.errorbar(value, y, xerr=[[value - row["ci_low_pct"]], [row["ci_high_pct"] - value]],
                                fmt=marker, markersize=5.5, color=color, elinewidth=1.6,
                                capsize=3, capthick=1.4, zorder=3)
            ax.set_yticks(range(len(items)), labels=[label for label, _ in items])
            ax.set_ylim(len(items) - .55, -.5)
            pad = (ticks[-1] - ticks[0]) * .026
            ax.set_xlim(ticks[0] - pad, ticks[-1] + pad)
            ax.xaxis.set_major_locator(FixedLocator(ticks))
            ax.xaxis.set_major_formatter(FuncFormatter(percent))
            ax.tick_params(axis="both", length=0, labelsize=10)
            ax.yaxis.set_tick_params(pad=7)
            ax.grid(axis="x", color=GRID, linewidth=.85, zorder=0)
            for spine in ax.spines.values():
                spine.set_visible(False)

        counts = {"Creatures": prices["n"], "Noncreatures": prices["noncreature"]["n"]}
        legend = [Line2D([], [], marker=marker, color=color, linewidth=1.7,
                         label=f"{name} ({counts[name]:,})") for name, color, marker in styles]
        fig.legend(handles=legend, loc="outside lower center", ncol=2, frameon=False, fontsize=10,
                   title="Adjusted difference in quoted price   ·   Lines show pointwise 95% intervals",
                   title_fontsize=10)
        fig.savefig(figures_dir / "card-feature-prices.png", dpi=160, facecolor=PAPER)
        plt.close(fig)


if __name__ == "__main__":
    render_card_features(Path("results/card_table/prices.json"), Path("figures"))
