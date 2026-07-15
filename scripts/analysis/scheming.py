import sys
import re

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
    
def weekly_returns(df: pd.DataFrame, type:str) -> pd.DataFrame:
    df = df.sort_values(["card_name", "date"])

    grouped = df.groupby("card_name")
    
    df["next_close"] = grouped["weekly_close"].shift(-1)
    df["return_date"] = grouped["date"].shift(-1)
    
    df["card_return"] = df["next_close"] / df["weekly_close"] - 1

    if type == "price":
        sorting_criteria = df["weekly_close"] >= 2 & df["card_return"].notna().copy()
    elif type == "rare":
        sorting_criteria = df["rarity"].isin(["rare", "mythic"])& df["card_return"].notna().copy()

    else:
        raise ValueError("Value must be either price or rare")


    weekly_returns = df.loc[sorting_criteria].groupby("return_date").agg(
    weekly_return =("card_return", "mean"),
    number_of_cards=("card_name", "nunique")
    ).reset_index()

    return weekly_returns

def calculate_index():
    pass

# So, what I can do now is to make the average price per week
# For that I need to make double index: card and date

def main():
    xln = pd.read_parquet(ixalan_path)
    print(xln.info())
    # plt.hist(xln["weekly_close"], bins=10)
    # plt.show()
     
    sns.kdeplot(data=np.log(xln["weekly_close"]), fill=True, cut=0)
    plt.xlabel("Price")
    plt.ylabel("Density")
    plt.title("Distribution of MTG card prices")
    # plt.show()
    mask = xln["weekly_close"] >=2
    rare_mask = xln["rarity"].isin(["rare", "mythic"])
    print(xln[mask]["weekly_close"].mean())
    print(xln[mask].count())
    print(xln[rare_mask].count()) 
    print(xln[rare_mask]["weekly_close"].mean())
    
    xln_portfolio = weekly_returns(xln, "price")
    xln_portfolio["portfolio_value"] = 100 * (1 + xln_portfolio["weekly_return"]).cumprod()
    print(xln_portfolio.head(25))

    xln_portfolio_rare = weekly_returns(xln, "rare")
    xln_portfolio_rare["portfolio_value"] = 100 * (1 + xln_portfolio_rare["weekly_return"]).cumprod()
    print(xln_portfolio_rare.head(25))



if __name__ == "__main__":
    main()


