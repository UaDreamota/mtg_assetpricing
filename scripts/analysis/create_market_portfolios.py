import pandas as pd
import pyarrow.parquet as pq
import sys
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


def greek_index_name(index: int) -> str:
    base = len(greek_names)
    parts = []
    index += 1
    while index:
        index -= 1
        parts.append(greek_names[index % base])
        index //= base
    return "_".join(reversed(parts))


def standard_index() -> dict:
    legality = pd.read_csv(legality_dir, sep=";")
    legality = process_legality(legality) 
    sets = create_sets_dates(cards_path)
        
    set_names = sets["set_name"].unique()
    legality_table_set_list = legality["Legal Sets"].tolist()
    greek_index = 0    

    dict = {}
    errors = 0
    for string in legality_table_set_list:
        active_greek = greek_index_name(greek_index)
        active_sets = []
        for set in set_names:
            if set in string:
                active_sets.append(set)
            else:
                errors += 1

        greek_index += 1
        dict[active_greek] = active_sets
    return dict, errors


def main():
    dict, errors = standard_index()
    print(dict)
    print(errors)
    pass


if __name__ == "__main__":
    main()
