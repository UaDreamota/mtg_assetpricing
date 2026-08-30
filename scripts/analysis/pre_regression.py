
import argparse
import sys
from pathlib import Path

import duckdb

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.analysis.final_dataman import sql_path

DATA_DIR = BASE_DIR / "data"

def grab_two_months():
    con = duckdb.connect()

    
    recent_df = con.execute(f"""
    SELECT *
    from read_parquet('{sql_path(DATA_DIR / "final_card_panel.parquet")}')
    WHERE month_start >= DATE '2026-01-01'
    AND month_start < DATE '2026-03-01'
    """).df()


    return recent_df

def create_lists_for_types(recent_df: pd.DataFrame):
   
    parts = recent_df["type_line"].str.replace(" — ", " ").str.split(n=1, expand=True)
    
    recent_df["card_types"] = parts[0]
    recent_df["subtypes"]   = parts[1]
    
    type_dummies = recent_df["card_types"].str.get_dummies(sep=" ")
    
    
    return pd.concat([recent_df, type_dummies], axis=1)


def regression_features(recent_df):
   
    #power cleaning
    recent_df["power_raw"] = recent_df["power"]
    recent_df["has_variable_power"] = recent_df["power"].isin(["*", "1+*"])
    recent_df["power_num"] = pd.to_numeric(recent_df["power"], errors="coerce")
   

    #tougness cleaning
    recent_df["toughness_raw"] = recent_df["toughness"]
    recent_df["has_variable_toughness"] = recent_df["toughness"].isin(["*", "1+*"])
    recent_df["toughness_num"] = pd.to_numeric(recent_df["toughness"], errors="coerce")

    return recent_df
    

# Things to do: The moving average of some of the things
# Better ways to calculate the banned 
# Explore all the list like columns
def main():
    
    pd.set_option("display.max_columns", None)
    # pd.set_option("display.width", None)
    # pd.set_option("display.max_colwidth", None)
    recent_df = grab_two_months()
    recent_df = regression_features(recent_df)
    recent_df = create_lists_for_types(recent_df)
    print(recent_df.sort_values("st_meta_exposure", ascending=False))
    print(recent_df.info())
    
    print(recent_df["mana_cost"].loc[[2,9000,255]])
    
    print(recent_df[["card_name","type_line"]].loc[[2,9000,255]])
    
    recent_df = create_lists_for_types(recent_df)
    
       

    pass

if __name__ == "__main__":
    main()
