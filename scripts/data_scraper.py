import re
from pathlib import Path

import pandas as pd

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


BASE_DIR = Path(__file__).resolve().parent.parent
api_url = "https://api.scryfall.com"
data_dir = BASE_DIR / "data"

# https://www.mtggoldfish.com/price/secrets-of-strixhaven/15/erode-foil#paper

# cards = pd.read_json(f"{data_dir}/default-cards-20260614090813.json")
# ixalan_cards = cards[cards["set_name"] == "Ixalan"]

xln = pd.read_csv(f"{data_dir}/xln_cards.csv")


def mtggoldfish_slug(value: object) -> str:
    slug = str(value).lower().replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def construct_html(card_name: str, set_name: str, collector_num: int, foil=False):
    base_html = "https://www.mtggoldfish.com/price/"
    
    card_name = re.sub(r"//.*","",card_name)
        
    set_html = mtggoldfish_slug(set_name)
    card_html = mtggoldfish_slug(card_name)
    foil_suffix = "-foil" if foil else ""
    return f"{base_html}{set_html}/{collector_num}/{card_html}{foil_suffix}#paper"

# Ok, I found one of the biggest problems, I don't really want to take the name of the card as an identifier, do I? 
# The problem is what do I use after as an identifier of the card? I would want to use the name, but I can accidentily take the price of a random reprint or the foil

def get_url(name: str, df: pd.DataFrame, foil=False):
    finish_col = "foil" if foil else "nonfoil"
    variants_df = df.loc[
        (df["name"] == name)
        & (df[finish_col])
        & (~df["reprint"])
    ]
    if variants_df.empty:
        raise ValueError(f"No {'foil' if foil else 'nonfoil'} non-reprint printing found for {name}")

    set_nm = variants_df["set_name"].iloc[0]
    collector = variants_df["collector_number"].iloc[0]
    final_html = construct_html(name, set_nm, collector, foil)
    return final_html

def main():
    print(xln.info()) 
    # construct_html("Favorable Winds", "Ixalan", 56, True)
    the_html = get_url(xln["name"].iloc[11], xln, True)
    print(the_html)
    return None

if __name__ == "__main__":
    main()
