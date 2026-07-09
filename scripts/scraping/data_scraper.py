import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


BASE_DIR = Path(__file__).resolve().parent.parent.parent
api_url = "https://api.scryfall.com"
data_dir = BASE_DIR / "data"

SET_SLUG_OVERRIDES = {
    "Magic 2014": "magic-2014-core-set",
    "Magic 2015": "magic-2015-core-set",
    "Marvel's Spider-Man": "marvel-super-heroes",
    "Modern Masters 2017": "modern-masters-2017-edition",
    "Murders at Karlov Manor": "ravnica-murders-at-karlov-manor",
    "The Lost Caverns of Ixalan": "lost-caverns-of-ixalan",
    "Time Spiral Timeshifted": "timeshifted",
}

SET_SLUG_OVERRIDES_BY_SLUG = {
    "magic-2014": "magic-2014-core-set",
    "magic-2015": "magic-2015-core-set",
    "marvels-spider-man": "marvel-super-heroes",
    "modern-masters-2017": "modern-masters-2017-edition",
    "murders-at-karlov-manor": "ravnica-murders-at-karlov-manor",
    "the-lost-caverns-of-ixalan": "lost-caverns-of-ixalan",
    "time-spiral-timeshifted": "timeshifted",
}

MULTI_FACE_SLUG_LAYOUTS = {
    "aftermath",
    "split",
}

OMIT_COLLECTOR_SET_SLUGS = {
    "alliances",
    "antiquities",
    "arabian-nights",
    "chronicles",
    "fallen-empires",
    "fifth-edition",
    "foreign-black-border",
    "fourth-edition",
    "fourth-edition-foreign-black-border",
    "homelands",
    "ice-age",
    "legends",
    "limited-edition-beta",
    "mirage",
    "revised-edition",
    "stronghold",
    "tempest",
    "the-dark",
    "unlimited-edition",
    "visions",
    "weatherlight",
}

# https://www.mtggoldfish.com/price/secrets-of-strixhaven/15/erode-foil#paper

cards_path = f"{data_dir}/default-cards-20260614090813.json"
# ixalan_cards = cards[cards["set_name"] == "Ixalan"]

# xln = pd.read_csv(f"{data_dir}/xln_cards.csv")


def ascii_fold(value: object) -> str:
    text = str(value)
    text = text.replace("’", "'").replace("`", "'")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
def mtggoldfish_slug(value: object) -> str:
    slug = ascii_fold(value).lower().replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def mtggoldfish_set_slug(value: object) -> str:
    set_name = str(value)
    if set_name in SET_SLUG_OVERRIDES:
        return SET_SLUG_OVERRIDES[set_name]
    slug = mtggoldfish_slug(set_name)
    return SET_SLUG_OVERRIDES_BY_SLUG.get(slug, slug)


def mtggoldfish_card_name(value: object, layout: object = None) -> str:
    card_name = str(value)
    card_name = re.sub(r"\s*<[^>]+>\s*", " ", card_name)
    layout_name = str(layout or "").lower()
    if "//" in card_name:
        if layout_name in MULTI_FACE_SLUG_LAYOUTS:
            card_name = re.sub(r"\s*//\s*", " ", card_name)
        else:
            card_name = re.sub(r"\s*//.*", "", card_name)
    return card_name.strip()


def mtggoldfish_collector_number(value: object) -> str:
    collector = str(value).strip()
    collector = collector.replace("★", "")
    collector = re.sub(r"^[A-Z0-9]{2,5}-", "", collector)
    return collector


def has_paper_usd_price(value: object) -> bool:
    if isinstance(value, dict):
        return bool(value.get("usd"))
    return False


def construct_html(card_name: str, set_name: str, collector_num: int, foil=False, layout=None):
    base_html = "https://www.mtggoldfish.com/price/"
    
    card_name = mtggoldfish_card_name(card_name, layout)
    collector_num = mtggoldfish_collector_number(collector_num)
        
    set_html = mtggoldfish_set_slug(set_name)
    card_html = mtggoldfish_slug(card_name)
    foil_suffix = "-foil" if foil else ""
    if set_html in OMIT_COLLECTOR_SET_SLUGS:
        return f"{base_html}{set_html}/{card_html}{foil_suffix}#paper"
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

# I need to construct my own legalities time series, as the "legality" is the current one


allowed_set_types = [
    "core",
    "expansion",
    "masters",
    "draft_innovation",
]
def html_pipeline(cards_path: str):
    cards = pd.read_json(cards_path)
    

    normal_sets_mask = cards["set_type"].isin(allowed_set_types)
    normal_sets = cards.loc[normal_sets_mask]
    normal_sets = normal_sets.loc[
        (~normal_sets["collector_number"].astype(str).str.startswith("A-"))
        & (~normal_sets["digital"])
        & (normal_sets["nonfoil"])
        & (normal_sets["games"].apply(lambda games: "paper" in games))
        & (normal_sets["prices"].apply(has_paper_usd_price))
        & (pd.to_datetime(normal_sets["released_at"]).dt.date <= date.today())
    ]
    normal_sets_unique = normal_sets["set_name"].unique()
    all_sets_ready = [mtggoldfish_set_slug(x) for x in normal_sets_unique]
    
    card_htmls = []

    for idx, row in normal_sets.iterrows():
        card = row["name"]
        set_name = row["set_name"]
        collector_number = row["collector_number"]
        layout = row.get("layout")
        
        temp_card = construct_html(card, set_name, collector_number, layout=layout)

        card_htmls.append(temp_card)
           
    return card_htmls

def create_sets_dates(cards_path: str) -> pd.DataFrame:
    cards = pd.read_json(cards_path)
    normal_sets_mask = cards["set_type"].isin(allowed_set_types)
    normal_sets = cards.loc[normal_sets_mask]
    normal_sets = normal_sets.loc[
        (~normal_sets["collector_number"].astype(str).str.startswith("A-"))
        & (~normal_sets["digital"])
        & (normal_sets["nonfoil"])
        & (normal_sets["games"].apply(lambda games: "paper" in games))
        & (normal_sets["prices"].apply(has_paper_usd_price))
        & (pd.to_datetime(normal_sets["released_at"]).dt.date <= date.today())
    ]
    normal_sets_unique = normal_sets["set_name"].unique()
    all_sets_ready = [mtggoldfish_set_slug(x) for x in normal_sets_unique]
    

    return normal_sets


def main():
    # construct_html("Favorable Winds", "Ixalan", 56, True)
    # the_html = get_url(xln["name"].iloc[11], xln, True)
    # print(the_html)
        
    # result = html_pipeline(f"{data_dir}/default-cards-20260614090813.json")
    # print(result)
    # print(len(result))
    sets = create_sets_dates(cards_path)
    set_names = sets["set_name"].unique()
    with open("legal_sets.txt", "a") as f:
        for set in set_names:
            f.write(set)
            f.write(",")
    return None

if __name__ == "__main__":
    main()
