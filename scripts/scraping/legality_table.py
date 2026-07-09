import pandas as pd
import pyarrow.parquet as pq
import requests 
from pathlib import Path
from io import StringIO
from rich import print as printr

BASE_DIR = Path(__file__).resolve().parent.parent.parent
legality_dir = BASE_DIR / "data/legality_table.csv"
parquet_dir = BASE_DIR / "data/price_histories.parquet"
endpoint = "https://mtg.fandom.com/api.php"

params = {
    "action": "parse",
    "page": "Standard/Timeline",
    "prop": "text",
    "format": "json",
}

def download_legality_table():
    response = requests.get(endpoint, params=params)
    print(response.text[:500])
    data = response.json()
    html = data["parse"]["text"]["*"]
    lazy_table = pd.read_html(StringIO(html))
    number = 0
    for table in lazy_table:
        printr(f"[bold red] Table number: {number}[/bold red]")
        print(table.head()) 
        number += 1
    
    legality_table = lazy_table[2]
    print(legality_table.info())
    printr(legality_table[2].sample(12))
    legality_table.to_csv("legality_table.csv")

    return legality_table

# Ok, the idea now is to transform the dates to proper 
# time format so that I can perform matching of sets
# This will allows me to estimate 
# Strip the commas! in dates
def process_legality(legality_table: pd.DataFrame) -> pd.DataFrame:
    
    df = legality_table
    df = df.drop(index=[1, 6, 14, 18, 66, 93, 100, 120, 140, 149])
    
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    df["Began"] = df["Began"].str.replace(",", "", regex = False)
    df["Ended"] = df["Ended"].str.replace(",", "", regex = False) 
   
    df["Began"] = pd.to_datetime(df["Began"], format="%b %-d %Y")
    df["Ended"] = pd.to_datetime(df["Ended"], format="%b %-d %Y")
    
    print(df.info())
    print(df.sample(5))
    return df 



def main():
    print(BASE_DIR)

    legality_table = pd.read_csv(legality_dir, sep=";")
    print(legality_table.info())
    printr(legality_table.sample(12))
    process_legality(legality_table)    
    prices = pq.ParquetFile(parquet_dir)
    print(prices.metadata)
    pass

if __name__ == "__main__":
    main()
