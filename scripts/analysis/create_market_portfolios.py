import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path
from rich import print as printr

BASE_DIR = Path(__file__).resolve().parent.parent.parent
legality_dir = BASE_DIR / "data/legality_table.csv"
parquet_dir = BASE_DIR / "data/price_histories.parquet"
    

def main():
    pass


if __name__ == "__main__":
    main()
