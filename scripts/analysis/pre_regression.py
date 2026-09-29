
import argparse
import re
import sys
from pathlib import Path

import duckdb

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.analysis.final_dataman import sql_path

DATA_DIR = BASE_DIR / "data"

COMMON_KEYWORD_MIN_CARDS = 50
RARE_KEYWORD_MAX_CARDS = 19

KEYWORD_GROUPS = {
    "evasion": {
        "Flying", "Menace", "Shadow", "Fear", "Intimidate", "Skulk",
        "Horsemanship", "Landwalk",
    },
    "combat_offense": {
        "Trample", "First strike", "Double strike", "Haste", "Exalted",
        "Battle cry", "Rampage", "Flanking", "Bushido", "Prowess",
    },
    "combat_defense": {
        "Vigilance", "Reach", "Defender", "Deathtouch", "Lifelink",
    },
    "protection_resilience": {
        "Hexproof", "Shroud", "Ward", "Indestructible", "Protection",
        "Regenerate", "Persist", "Undying",
    },
    "graveyard_recursion": {
        "Flashback", "Escape", "Unearth", "Dredge", "Retrace",
        "Jump-start", "Disturb", "Embalm", "Eternalize", "Aftermath",
        "Encore",
    },
    "alternative_cost": {
        "Affinity", "Convoke", "Delve", "Improvise", "Emerge", "Evoke",
        "Kicker", "Buyback", "Dash", "Bestow", "Overload", "Ninjutsu",
    },
    "casting_timing": {
        "Flash", "Split second", "Suspend", "Foretell", "Plot", "Rebound",
        "Cascade", "Discover",
    },
    "card_selection_advantage": {
        "Scry", "Surveil", "Investigate", "Connive", "Learn", "Explore",
        "Cycling",
    },
    "counters_growth": {
        "Proliferate", "Evolve", "Adapt", "Modular", "Mentor", "Training",
        "Outlast", "Bolster",
    },
    "tokens_resources": {
        "Treasure", "Food", "Clue", "Populate", "Amass", "Incubate",
        "Fabricate", "Role token",
    },
    "transformation_hidden": {
        "Transform", "Daybound", "Nightbound", "Morph", "Megamorph",
        "Manifest", "Manifest dread", "Disguise", "Cloak",
    },
    "multiplayer_commander": {
        "Partner", "Partner with", "Background", "Choose a background",
        "Myriad", "Goad", "Monarch", "Initiative", "Assist",
        "Doctor's companion",
    },
    "drawback_or_decay": {
        "Cumulative upkeep", "Fading", "Vanishing", "Decayed", "Echo",
        "Defender",
    },
}


def safe_feature_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")

def grab_two_months():
    con = duckdb.connect()

    
    recent_df = con.execute(f"""
    SELECT *
    from read_parquet('{sql_path(DATA_DIR / "final_card_panel.parquet")}')
    WHERE month_start >= DATE '2026-01-01'
    AND month_start < DATE '2026-03-01'
    """).df()


    return recent_df

def create_lists_for_types(recent_df: pd.DataFrame):
   
    parts = recent_df["type_line"].fillna("").str.partition(" — ")
    
    recent_df["card_types"] = parts[0]
    recent_df["subtypes"] = parts[2].replace("", pd.NA)
    
    type_dummies = recent_df["card_types"].str.get_dummies(sep=" ")
    
    
    return pd.concat([recent_df, type_dummies], axis=1)


def regression_features(recent_df):
   
    #power cleaning
    recent_df["power_raw"] = recent_df["power"]
    recent_df["has_variable_power"] = recent_df["power"].isin(["*", "1+*"])
    recent_df["power_num"] = pd.to_numeric(recent_df["power"], errors="coerce")
   
    
    #tougness cleaning
    recent_df["toughness_raw"] = recent_df["toughness"]
    recent_df["has_variable_toughness"] = recent_df["toughness"].isin(["*", "1+*"])
    recent_df["toughness_num"] = pd.to_numeric(recent_df["toughness"], errors="coerce")

    recent_df["loyalty_raw"] = recent_df["loyalty"]
    recent_df["loyalty_num"] = pd.to_numeric(recent_df["loyalty"], errors="coerce")
    recent_df["has_variable_loyalty"] = (
        recent_df["loyalty"].notna() & recent_df["loyalty_num"].isna()
    )

    recent_df["defense_raw"] = recent_df["defense"]
    recent_df["defense_num"] = pd.to_numeric(recent_df["defense"], errors="coerce")
    recent_df["has_variable_defense"] = (
        recent_df["defense"].notna() & recent_df["defense_num"].isna()
    )

    return recent_df


def text_and_keyword_features(df: pd.DataFrame):
    recent_df = df
    text = recent_df["oracle_text"].fillna("")

    recent_df["has_oracle_text"] = text.str.strip().ne("")
    recent_df["oracle_char_count"] = text.str.len()
    recent_df["oracle_word_count"] = text.str.findall(r"\b[\w’'-]+\b").str.len()
    recent_df["oracle_paragraph_count"] = text.apply(
        lambda value: sum(bool(line.strip()) for line in value.splitlines())
    )
    recent_df["mana_symbols_in_text"] = text.str.count(r"\{[^{}]+\}")
    recent_df["numeric_mentions"] = text.str.count(r"\b\d+\b")
    recent_df["modal_choice_count"] = text.str.count(r"(?m)^•")

    def list_or_empty(value):
        if isinstance(value, list):
            return value
        if hasattr(value, "tolist"):
            converted = value.tolist()
            return converted if isinstance(converted, list) else []
        return []

    recent_df["keywords"] = recent_df["keywords"].apply(list_or_empty)
    recent_df["keyword_count"] = recent_df["keywords"].str.len()

    unique_card_keywords = (
        recent_df[["card_name", "keywords"]]
        .drop_duplicates("card_name")["keywords"]
        .explode()
        .dropna()
    )
    keyword_frequency = unique_card_keywords.value_counts()
    common_keywords = set(
        keyword_frequency[
            keyword_frequency >= COMMON_KEYWORD_MIN_CARDS
        ].index
    )
    rare_keywords = set(
        keyword_frequency[
            keyword_frequency <= RARE_KEYWORD_MAX_CARDS
        ].index
    )
    middle_keywords = set(keyword_frequency.index) - common_keywords - rare_keywords

    keyword_sets = recent_df["keywords"].apply(set)
    keyword_feature_data = {}

    for keyword in sorted(common_keywords):
        column_name = f"keyword_{safe_feature_name(keyword)}"
        keyword_feature_data[column_name] = keyword_sets.apply(
            lambda values, keyword=keyword: int(keyword in values)
        ).astype("int8")

    for group_name, group_keywords in KEYWORD_GROUPS.items():
        keyword_feature_data[f"kw_group_{group_name}"] = keyword_sets.apply(
            lambda values, members=group_keywords: int(bool(values & members))
        ).astype("int8")

    mapped_keywords = set().union(*KEYWORD_GROUPS.values())
    unmapped_middle_keywords = middle_keywords - mapped_keywords

    keyword_feature_data["rare_keyword_count"] = keyword_sets.apply(
        lambda values: len(values & rare_keywords)
    )
    keyword_feature_data["has_rare_keyword"] = keyword_feature_data[
        "rare_keyword_count"
    ].gt(0)
    keyword_feature_data["unmapped_middle_keyword_count"] = keyword_sets.apply(
        lambda values: len(values & unmapped_middle_keywords)
    )
    keyword_features = pd.DataFrame(keyword_feature_data, index=recent_df.index)
    recent_df = pd.concat([recent_df, keyword_features], axis=1)

    return recent_df


def temporal_features(df: pd.DataFrame):
    recent_df = df
    month_start = pd.to_datetime(recent_df["month_start"])
    first_released_at = pd.to_datetime(recent_df["first_released_at"])

    card_age_months = (
        (month_start.dt.year - first_released_at.dt.year) * 12
        + month_start.dt.month
        - first_released_at.dt.month
    )
    recent_df["card_age_months"] = card_age_months.where(card_age_months >= 0)

    return recent_df



def mana_cost_handling(df: pd.DataFrame): 
    recent_df = df 
    recent_df["mana_list"] = recent_df["mana_cost"].str.findall(r"\{([^{}]+)\}")
    recent_df["mana_list"] = recent_df["mana_list"].apply(
        lambda value: value if isinstance(value, list) else []
    )

    recent_df["is_x_spell"] = recent_df["mana_list"].apply(lambda x: "X" in x) 

    colors = {"W", "U", "B", "R", "G"}
    noncolor_alternatives = {"2", "P", "C"}

    def count_colored_pip_slots(symbols):
        return sum(
            bool(colors.intersection(symbol.split("/")))
            for symbol in symbols
        )

    def count_required_colored_pips(symbols):
        required = 0
        for symbol in symbols:
            alternatives = set(symbol.split("/"))
            has_color = bool(alternatives.intersection(colors))
            can_avoid_color = bool(alternatives.intersection(noncolor_alternatives))
            if has_color and not can_avoid_color:
                required += 1
        return required

    recent_df["colored_pip_slots"] = recent_df["mana_list"].apply(
        count_colored_pip_slots
    )
    recent_df["required_colored_pips"] = recent_df["mana_list"].apply(
        count_required_colored_pips
    )
    recent_df["colored_pip_share"] = (
        recent_df["colored_pip_slots"]
        / recent_df["cmc"].where(recent_df["cmc"] > 0)
    )

    def count_color_pips(symbols, color):
        return sum(color in symbol.split("/") for symbol in symbols)

    def has_color(colors, color):
        try:
            return color in colors
        except (TypeError, ValueError):
            return False

    color_names = {
        "R": "red",
        "G": "green",
        "B": "black",
        "W": "white",
        "U": "blue",
    }
    for color, name in color_names.items():
        recent_df[f"{name}_pips"] = recent_df["mana_list"].apply(
            lambda symbols, color=color: count_color_pips(symbols, color)
        )
        recent_df[f"is_{name}_spell"] = recent_df["colors"].apply(
            lambda colors, color=color: has_color(colors, color)
        )
        recent_df[f"identity_{name}"] = recent_df["color_identity"].apply(
            lambda identity, color=color: has_color(identity, color)
        )

    color_flags = [f"is_{name}_spell" for name in color_names.values()]
    identity_flags = [f"identity_{name}" for name in color_names.values()]
    recent_df["color_count"] = recent_df[color_flags].sum(axis=1)
    recent_df["identity_color_count"] = recent_df[identity_flags].sum(axis=1)
    recent_df["is_multi_color"] = recent_df[color_flags].sum(axis=1).ge(2)
    recent_df["is_colorless"] = recent_df["color_count"].eq(0)
    recent_df["has_multicolor_identity"] = recent_df[
        "identity_color_count"
    ].ge(2)

    print(recent_df[["mana_list", "card_name", "mana_cost", "is_multi_color"]].head(15))
    
    return recent_df
    
    

# Things to do: The moving average of some of the things
# Better ways to calculate the banned 
# Explore all the list like columns
def main():
    
    pd.set_option("display.max_columns", None)
    # pd.set_option("display.width", None)
    # pd.set_option("display.max_colwidth", None)
    recent_df = grab_two_months()
    recent_df = regression_features(recent_df)
    recent_df = create_lists_for_types(recent_df)
    recent_df = mana_cost_handling(recent_df) 
    recent_df = text_and_keyword_features(recent_df)
    recent_df = temporal_features(recent_df)
    print(recent_df.sort_values("st_meta_exposure", ascending=False))
    print(recent_df.info())
    
    print(recent_df["mana_cost"].loc[[2,9000,255]])
    
    print(recent_df[["card_name","type_line"]].loc[[2,9000,255]])
      
    print(recent_df.info())

    print(recent_df[recent_df["card_name"].isin(["Island", "Mountain", "Forest", "Plains", "Swamp"])][["le_performance_exposure", "card_name"]])
    
    pass

if __name__ == "__main__":
    main()
