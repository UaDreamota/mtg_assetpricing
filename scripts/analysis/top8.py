import re
import sys

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pandas as pd 
import pyarrow.parquet as pq


from scripts.scraping.legality_table import load_legality_with_greeks

st_decks = BASE_DIR / "data/mtgtop8/mtgtop8_decks"
st_cards = BASE_DIR / "data/mtgtop8/mtgtop8_cards"

mo_decks = BASE_DIR /"data/modern/mtgtop8_decks"
mo_cards = BASE_DIR /"data/modern/mtgtop8_cards" 

vi_decks = BASE_DIR /"data/vintage/mtgtop8_decks"
vi_cards = BASE_DIR /"data/vintage/mtgtop8_cards" 

pau_decks = BASE_DIR /"data/pauper/mtgtop8_decks"
pau_cards = BASE_DIR /"data/pauper/mtgtop8_cards" 

le_decks = BASE_DIR /"data/legacy/mtgtop8_decks"
le_cards = BASE_DIR /"data/legacy/mtgtop8_cards" 

cedh_decks = BASE_DIR /"data/cedh/mtgtop8_decks"
cedh_cards = BASE_DIR /"data/cedh/mtgtop8_cards" 




DATA_DIR = BASE_DIR / "data"

def load_parquets(path: Path):
    dfs = []
    for file in sorted(path.glob("*.parquet")):
        df = pd.read_parquet(file)
        dfs.append(df)

    final_parquet = pd.concat(dfs, ignore_index=True)
    
    return final_parquet

# What do I do with all the information that I have? 
# I think the idea now is to calculate "card exposure to meta". 
# First of all: does this card play in meta decks (and which format)
# Second of all: mainboard, sideboard(?)
# Third of all: how many different decks does it play in: (unversality)?

# Question: How well can I differentiate cards in one "deck" and "archetype"

# First task: Get the archetype names per standard (and persistence over month)

def cards_to_deck(decks: pd.DataFrame, cards: pd.DataFrame, time_mask_beg="2011-01-01", time_mask_end = "2026-07-30"):
    decks["event_date"] = pd.to_datetime(decks["event_date"])
    time_mask_beg = pd.to_datetime(time_mask_beg)
    time_mask_end = pd.to_datetime(time_mask_end)

    
    decks["month"] = decks["event_date"].dt.to_period("M")
    decks = decks[(decks["event_date"] >= time_mask_beg) & (decks["event_date"] <= time_mask_end)]

    
    archetypes = decks["archetype"].unique()
    print(archetypes) 


    # Because TOP-8 has some Arena contamination card.
    cards = cards.copy()
    cards["card_name_raw"] = cards["card_name"]
    cards["card_name"] = cards["card_name"].str.replace(r"^A-", "", regex=True)


    cards = cards.merge(decks[["deck_id","archetype", "month"]], on="deck_id",how="left")
    cards = cards.dropna(subset=["archetype"])
    print(cards.info())
    print(cards.head())
    print(decks["archetype"].value_counts())

    return cards

def get_finalist_rate(decks:pd.DataFrame): 
     
    decks["event_date"] = pd.to_datetime(decks["event_date"])
    # decks["rank"] = decks["rank"].astype(int)
    archetypes = decks["archetype"].unique()

    decks["month"] = decks["event_date"].dt.to_period("M")
    decks["won"] = decks["rank"].isin(["1","2"])
   
    monthly_winrate = decks.groupby(["month","archetype"])["won"].agg(["mean","size"]).reset_index()
    
    monthly_winrate["share"] = monthly_winrate["size"] / monthly_winrate.groupby("month")["size"].transform("sum")
    monthly_winrate = monthly_winrate.rename(columns={"mean":"performance"})
    monthly_winrate = monthly_winrate[monthly_winrate["size"] >= 5]
    # monthly_winrate = monthly_winrate.groupby()
    
    return monthly_winrate

def card_exposure(cards:pd.DataFrame):
     
    card_presence = cards[["deck_id", "archetype","card_name", "month"]].drop_duplicates().groupby(["archetype", "card_name", "month"]).size().reset_index(name="deck_count")

    archetype_presence = cards[["archetype","deck_id", "month"]].drop_duplicates().groupby(["archetype", "month"]).size().reset_index(name="archetype_decks")

    exposure = card_presence.merge(archetype_presence, on=["month", "archetype"], how="left")
    exposure["deck_share"] = exposure["deck_count"] / exposure["archetype_decks"]

    return exposure

def calculating_the_e(cards: pd.DataFrame, wr:pd.DataFrame, index:str):

    card_score = cards.merge(wr[["month", "archetype", "share", "performance"]], on=["month", "archetype"], how="left")
    
    card_score[f"{index}_meta_contribution"] = card_score["share"] * card_score["deck_share"]  
    card_score[f"{index}_performance_contribution"] = card_score["share"] *  card_score["performance"] * card_score["deck_share"]


    card_score = card_score.groupby(["month", "card_name"], as_index=False).agg(
        **{
            f"{index}_meta_exposure": (f"{index}_meta_contribution", "sum"),
            f"{index}_performance_exposure": (f"{index}_performance_contribution", "sum"),
        }
    )

    return card_score

    # Intended to be the master function that just ouputs all the card performance and metagame per format and aggregates
def just_per_format(format_pd: Path, card_format: Path, index:str):
    
    format_pd = load_parquets(format_pd)
    card_format = load_parquets(card_format)
    cards = cards_to_deck(format_pd, card_format)
    cards = card_exposure(cards)
    
    wr = get_finalist_rate(format_pd)
    
    
    score = calculating_the_e(cards, wr, index)

    return score
    
   
def join_on_format(old_score, new_score):
        
    joined_pd = old_score.merge(new_score, on=["card_name", "month"], how="outer")

    
    return joined_pd


def main():
    
    st = just_per_format(st_decks, st_cards, "st")
    vi = just_per_format(vi_decks, vi_cards, "vi")
    mo = just_per_format(mo_decks, mo_cards, "mo")
    le = just_per_format(le_decks, le_cards, "le")
    cedh = just_per_format(cedh_decks, cedh_cards, "cedh")
    pau = just_per_format(pau_decks, pau_cards, "pau")

    
    st_vi = join_on_format(st, vi)
    vi_mo = join_on_format(st_vi, mo)
    lele = join_on_format(vi_mo, le)
    cece = join_on_format(lele, cedh)
    final = join_on_format(cece, pau)

    
    print(final.info())
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", None)
    pd.set_option("display.max_colwidth", None)
    print(final[final["card_name"] == "Brainstorm"].tail(50))
    final.to_parquet("data/all_f_exposure.parquet")

    return None

if __name__ == "__main__":
    main()
