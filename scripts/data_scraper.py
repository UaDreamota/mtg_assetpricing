import requests
from pathlib import Path 

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
api_url = "https://api.scryfall.com"
data_dir = BASE_DIR / "data"

# https://www.mtggoldfish.com/price/secrets-of-strixhaven/15/erode-foil#paper

# cards = pd.read_json(f"{data_dir}/default-cards-20260614090813.json")
# ixalan_cards = cards[cards["set_name"] == "Ixalan"]

xln = pd.read_csv(f"{data_dir}/xln_cards.csv")

def construct_html():
    pass

def main():
    print(xln.info()) 
    return None

if __name__ == "__main__":
    main()
