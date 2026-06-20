
from pathlib import Path
from playwright.async_api import async_playwright
import pandas as pd 

BASE_DIR = Path(__file__).resolve().parent.parent
api_url = "https://api.scryfall.com"
data_dir = BASE_DIR / "data"

cards = pd.read_json(f"{data_dir}/default-cards-20260614090813.json")

# Ok, the idea would be to replicate the html of the MtgGoldfish so I could scrape the prices. 
# The main problem is that their url is pretty unfriendly, here's an example:
# https://www.mtggoldfish.com/price/secrets-of-strixhaven/15/erode-foil#paper
# So, it's /price/name-of-the-set/collector_number/name_of_the_card#format
# All-in-all I can just use the dictionary to tranlsate the set name to the proper analogue
# Then I just scrape the whole thing into pickle or parquet. 
# Probably, I would rather do monthly close data. 

def main():
    # print(cards.info())
    # print(cards[cards["reprint"] == False].sample(2))
    matches = cards.loc[cards["name"] == "Erode", "collector_number"]
    # print(cards["collector_number"].sample(5))    
    print(matches.sample(len(matches)))
    print(cards['set_name'].unique())
    print(cards['set'].unique())
    print("Done!")
    return None


if __name__ == "__main__":
    main()
