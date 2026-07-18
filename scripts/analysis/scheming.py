import sys
import re
import time


import numpy as np
import pandas as pd
import seaborn as sns
import pyarrow.parquet as pq

import matplotlib.pyplot as plt

import duckdb
from pathlib import Path
from rich import print as printr

import statistics

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(BASE_DIR))

data_dir = BASE_DIR / "data"

legality_dir = BASE_DIR / "data/legality_table.csv"
parquet_dir = BASE_DIR / "data/price_histories.parquet"
cards_path = f"{data_dir}/default-cards-20260614090813.json"
data_path = BASE_DIR / "data" / "standards"
ixalan_path = BASE_DIR / "data/ixalan_weekly.parquet"
modern_path = BASE_DIR / "data" / "modern_prices.parquet"

def load_weekly_prices(path: Path) -> pd.DataFrame:
    columns = set(pq.read_schema(path).names)
    if "weekly_close" in columns:
        return pd.read_parquet(path)

    required_columns = {"card_name", "rarity", "date", "price"}
    missing_columns = required_columns - columns
    if missing_columns:
        raise ValueError(
            f"{path} is missing required columns: {sorted(missing_columns)}"
        )

    # Support older generated files that contain daily prices. Doing this in
    # DuckDB avoids loading the full daily dataset into pandas first.
    connection = duckdb.connect()
    try:
        return connection.execute(
            """
            SELECT
                card_name,
                rarity,
                CAST(
                    time_bucket(INTERVAL '1 week', date + INTERVAL '2 days')
                    + INTERVAL '4 days'
                    AS DATE
                ) AS date,
                arg_max(price, date) AS weekly_close
            FROM read_parquet(?)
            GROUP BY card_name, rarity, 3
            """,
            [str(path)],
        ).df()
    finally:
        connection.close()


def straight_average(data_path: str, type: str) -> pd.DataFrame:
    index = [] 
    for file in data_path.glob("*.parquet*"):
        temp_df = pd.read_parquet(file)
        printr("[magenta3]YEAH[/magenta3]")   
        if type == "mean":
            mask = temp_df["weekly_close"] >=2
            thing = temp_df[mask]["weekly_close"].mean()
            index.append(thing)                

        elif type == "rare":
            mask = temp_df["rarity"].isin(["rare", "mythic"])
            thing = temp_df[mask]["weekly_close"].mean()
            index.append(thing)
        else:
            raise ValueError("Type should be either mean or rare")
    
    
    # all_times = pd.concat(index)
    all_times = index
    return all_times
    
def weekly_returns(df: pd.DataFrame, type:str, filter=2) -> pd.DataFrame:
    df = df.sort_values(["card_name", "date"])

    grouped = df.groupby("card_name")
    
    df["next_close"] = grouped["weekly_close"].shift(-1)
    df["return_date"] = grouped["date"].shift(-1)
    
    df["card_return"] = df["next_close"] / df["weekly_close"] - 1

    if type == "price":
        sorting_criteria = (
            (df["weekly_close"] >= filter)
            & df["card_return"].notna()
        )
    elif type == "rare":
        sorting_criteria = df["rarity"].isin(["rare", "mythic"])& df["card_return"].notna().copy()

    else:
        raise ValueError("Value must be either price or rare")


    weekly_returns = df.loc[sorting_criteria].groupby("return_date").agg(
    weekly_return =("card_return", "mean"),
    number_of_cards=("card_name", "nunique")
    ).reset_index()

    return weekly_returns

def calculate_index(path_to_standards:str):
           

    portfolios = []

    for file in path_to_standards.glob("*.parquet*"):
        
        start = time.time()
        temp_df = pd.read_parquet(file) 
        portfolio_temp = weekly_returns(temp_df, "price")
        
        portfolios.append(portfolio_temp)
        
        print(f"Execution time: {time.time() - start:.2f} seconds")

        
    whole_portfolio = pd.concat(portfolios, ignore_index = True)
    whole_portfolio = whole_portfolio.sort_values("return_date")
    whole_portfolio = calculate_portfolio_value(whole_portfolio) 

    return whole_portfolio

def calculate_portfolio_value(whole_portfolio: pd.DataFrame):
    
    whole_portfolio["portfolio_value"] = 100 * (1 + whole_portfolio["weekly_return"]).cumprod()
    
    return whole_portfolio

# What is the next logical step? Allow the portfolio sorts.
# Zero thing: Non-standard index (keep the high price cards)
# First thing: deck metagame
# Second thing: Keyword existence*
# Third thing: Word count + Keyword count (Quantiles of word counts sounds fun)
# Fourth thing: Model synergies 
# Fifth thing: 


def main():
    
    modern_prices = load_weekly_prices(modern_path)

    whole_portfolio = calculate_index(data_path)
    print(whole_portfolio.head())
    
    print(whole_portfolio.info())
    modern_portfolio = weekly_returns(modern_prices, "price", filter=2)
    modern_portfolio = calculate_portfolio_value(modern_portfolio)

if __name__ == "__main__":
    main()
