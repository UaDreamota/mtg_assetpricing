import sys
import re
import time

import pandas as pd
import pyarrow.parquet as pq

import duckdb
from pathlib import Path
from rich import print as printr

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

from scripts.scraping.legality_table import process_legality
from scripts.scraping.data_scraper import create_sets_dates



data_dir = BASE_DIR / "data"

legality_dir = BASE_DIR / "data/legality_table.csv"
parquet_dir = BASE_DIR / "data/price_histories.parquet"
cards_path = f"{data_dir}/default-cards-20260614090813.json"

# I think that the best way is to create 3 indicies:
# Standard index with metagame and legal sets values
# Modern index with metagame and value weighted indecies?

# I think a good extension is to look at the booster box prices
# I really need to read thos papers on the portfolio formation


greek_names = [
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta",
    "Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu",
    "Nu", "Xi", "Omicron", "Pi", "Rho", "Sigma",
    "Tau", "Upsilon", "Phi", "Chi", "Psi", "Omega"
]


MANUAL_ALIASES = {
      "Revised": "Revised Edition",
      "4th Edition": "Fourth Edition",
      "5th edition": "Fifth Edition",
      "5th Edition": "Fifth Edition",
      "6th edition": "Classic Sixth Edition",
      "6th Edition": "Classic Sixth Edition",
      "7th edition": "Seventh Edition",
      "7th Edition": "Seventh Edition",
      "8th edition": "Eighth Edition",
      "8th Edition": "Eighth Edition",
      "9th Edition": "Ninth Edition",
      "10th Edition": "Tenth Edition",
      "M10": "Magic 2010",
      "M11": "Magic 2011",
      "M12": "Magic 2012",
      "M13": "Magic 2013",
      "M14": "Magic 2014",
      "M15": "Magic 2015",
      "MoM:The Aftermath": "March of the Machine: The Aftermath",
      "MoM: The Aftermath": "March of the Machine: The Aftermath",
      "Brother's War": "The Brothers' War",
      "Battle For Zendikar": "Battle for Zendikar",
      "BFZ": "Battle for Zendikar",
      "OGW": "Oath of the Gatewatch",
      "Shadows Over Innistrad": "Shadows over Innistrad",
      "SOI": "Shadows over Innistrad",
      "EMN": "Eldritch Moon",
      "Journey Into Nyx": "Journey into Nyx",
      "Theros: Beyond Death": "Theros Beyond Death",
      "Ikoria: L.O.B.": "Ikoria: Lair of Behemoths",
      "Ikoria: L.O.B.s": "Ikoria: Lair of Behemoths",
      "Strixhaven": "Strixhaven: School of Mages",
      "Str of New Capenna": "Streets of New Capenna",
      "D&D Forgotten Realms": "Adventures in the Forgotten Realms",
      "Midnight Hunt": "Innistrad: Midnight Hunt",
      "Crimson Vow": "Innistrad: Crimson Vow",
      "Neon Dynasty": "Kamigawa: Neon Dynasty",
      "New Capenna": "Streets of New Capenna",
      "All Will Be One": "Phyrexia: All Will Be One",
      "Phyrexia: All Will be One": "Phyrexia: All Will Be One",
      "Lost Caverns of Ixalan": "The Lost Caverns of Ixalan",
      "Outlaws of Thunder Jct": "Outlaws of Thunder Junction",
      "Duskmourn": "Duskmourn: House of Horror",
  }


con = duckdb.connect()
con.execute(f"""
      CREATE OR REPLACE VIEW prices AS
      SELECT *
      FROM '{parquet_dir.resolve().as_posix()}'
  """)

def greek_index_name(index: int) -> str:
    base = len(greek_names)
    parts = []
    index += 1
    while index:
        index -= 1
        parts.append(greek_names[index % base])
        index //= base
    return "_".join(reversed(parts))


# And I need to append the greeks to the original dataset, it gives dates
def standard_greeks():
    legality = pd.read_csv(legality_dir, sep=";")
    legality = process_legality(legality) 
    sets = create_sets_dates(cards_path)
        
    set_names = sorted(sets["set_name"].unique(), key=len, reverse = True)
    legality_table_set_list = legality["Legal Sets"].tolist()
    greek_index = 0    
    dict = {}
    for string in legality_table_set_list:
        active_greek = greek_index_name(greek_index)
        active_sets = [] 
        remaining = string
        
        for allias, canonical in MANUAL_ALIASES.items():
            if allias in remaining:
                active_sets.append(canonical)
                remaining = remaining.replace(allias, "")

        for set in set_names:
            if set in remaining:
                active_sets.append(set)
                remaining = remaining.replace(set, "")

        dict[active_greek] = active_sets
        legality.loc[legality.index[greek_index], "Greek"]= active_greek
        greek_index += 1

    legality = legality.set_index("Greek")
    return dict, legality


def greek_card_pool(name_of_greek:str, greeks:dict, cards:str) -> pd.DataFrame:
    legal_sets = greeks[name_of_greek]
    card_json = pd.read_json(cards)
   
    standard_cards_mask =card_json["set_name"].isin(legal_sets)
    standard_cards = card_json.loc[standard_cards_mask]
    
        
    return standard_cards

#Maybe it's a good idea to add a pre-month bias
#Usually, the cards have pre-sale prices, those
#that would be just before the set enters legality pools. So, it could be nice to write a function that would also capture the pre-sale prices.
def fetch_stadard_prices(name_of_greek:str, greeks:dict, cards:str, legality_table: pd.DataFrame) -> pd.DataFrame:
    greeks_dict = greeks
    cards_json = cards
    standard_cards = greek_card_pool(name_of_greek, greeks_dict, cards_json)[["name", "rarity"]].drop_duplicates()
    final_df = pd.DataFrame()
    
    began = legality_table.loc[name_of_greek, "Began"]
    ended = legality_table.loc[name_of_greek, "Ended"]
    con.register("standard_cards", standard_cards)
    query_df = con.execute(
            """
            SELECT card_name, card_id, price, date, rarity
            from prices AS p
            JOIN standard_cards AS c
            ON p.card_name = c.name
            WHERE p.date BETWEEN ? AND ?
            """,[began, ended]).df()
    print(f"finished")
    final_df = pd.concat([final_df, query_df]) 
    
    return final_df
# What I would do is probably just create one function that would output

def get_weekly_standard():
    
    dict, legality_table = standard_greeks()
    for standard_rotation in dict.keys():
        
        start = time.time()
        
        standard = greek_card_pool(standard_rotation, dict, cards_path)
        result_db = fetch_stadard_prices(standard_rotation, dict, cards_path, legality_table)
        result_weekly = result_db.groupby(["card_name", "rarity", pd.Grouper(key="date", freq="W-FRI")])["price"].last().reset_index(name="weekly_close")
        result_weekly.to_parquet(f"data/{standard_rotation}_weekly.parquet") 
        print(f"Written {standard_rotation}")
        print(f"Execution time: {time.time() - start:.2f} seconds")
    pass

def grab_modern_index(cards_prices: Path):
    
    price_parquet = pd.read_json(cards_prices)[["name","rarity"]].drop_duplicates()
    printr("[yellow]Loaded data[/yellow]")
    con.register("cards", price_parquet)
    printr("[blue]Starting query[/blue]")
    query_df = con.execute(
            """
            SELECT card_name, card_id, price, date, rarity
            from prices AS p
            JOIN cards AS c
            ON p.card_name = c.name
            WHERE p.price > 2
            """).df()
    
    printr("[magenta3]Ended query[/magenta3]")

    result_weekly = query_df.groupby(["card_name", "rarity", pd.Grouper(key="date", freq="W-FRI")])["price"].last().reset_index(name="weekly_close")
        

    return result_weekly

def main():
        
        # start_whole = time.time()
        # get_weekly_standard()
        # print(f"Execution time of the whole function: {time.time() - start_whole:.2f} seconds")
        df = grab_modern_index(cards_path)
        print(df.head())
        df.to_parquet("data/modern_prices.parquet")
        
        printr("[magenta3]File saved[/magenta3]")
        pass


if __name__ == "__main__":
    main()
