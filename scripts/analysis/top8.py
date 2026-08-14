import re
import sys


import pandas as pd 
from pathlib import Path
import pyarrow.parquet as pq


from scripts.scraping.legality_table import load_legality_with_greeks

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))


st_decks = BASE_DIR / "data/mtgtop8/mtgtop8_decks"
st_cards = BASE_DIR / "data/mtgtop8/mtgtop8_cards"

mo_decks = BASE_DIR /"data/modern/mtgtop8_decks"
mo_cards = BASE_DIR /"data/modern/mtgtop8_cards" 

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

    decks = decks[(decks["event_date"] >= time_mask_beg) & (decks["event_date"] <= time_mask_end)]
    
    archetypes = decks["archetype"].unique()
    print(archetypes) 


    cards = cards.merge(decks[["deck_id","archetype"]], on="deck_id",how="left")
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
     
    card_presence = cards[["deck_id", "archetype","card_name"]].drop_duplicates().groupby(["archetype", "card_name"]).size().reset_index(name="deck_count")

    archetype_presence = cards[["archetype","deck_id"]].drop_duplicates().groupby("archetype").size().reset_index(name="archetype_decks")

    exposure = card_presence.merge(archetype_presence, on="archetype", how="left")
    exposure["deck_share"] = exposure["deck_count"] / exposure["archetype_decks"]



    return exposure


def main():
    standard_decks = load_parquets(st_decks)
    standard_cards = load_parquets(st_cards)    

    legality = pd.read_csv(DATA_DIR/"legality_proc.csv")
    # print(standard_decks.head)
    # print(standard_decks.info())
    # print(standard_cards.info())
    # print(standard_cards.head)
    
    # legality = load_legality_with_greeks(BASE_DIR /"data" /"legality_table.csv")
    # legality.to_csv(BASE_DIR/"data/legality_proc.csv")    

    print(legality.info())    
    # print(legality[legality["Greek"] == "Delta_Alpha"])
    date_mask_beg = "2017-09-29"
    date_mask_end = "2018-01-18"

    mask = legality["Greek"] == "Delta_Alpha"
    print(type(mask))

    cards_to_deck(standard_decks, standard_cards, date_mask_beg, date_mask_end) 
    print(standard_decks.info())
    print(standard_decks["rank"].value_counts())

    
    # I got archetype winrate, now to the share   

    wr = get_finalist_rate(standard_decks)
    
    # print(wr.groupby("month").head(15))
    # print(wr[wr["archetype"] == "Izzet Prowess"])

    cards = cards_to_deck(standard_decks, standard_cards)
    print(cards.info())
    print(cards.head())
     
    cards = card_exposure(cards)
    
    print(cards.info())
    print(cards[cards["archetype"] == "Izzet Prowess"].head(25))

    return None

if __name__ == "__main__":
    main()
