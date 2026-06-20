import re
from pathlib import Path

import pandas as pd

from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


BASE_DIR = Path(__file__).resolve().parent.parent

# It could be the case that I can also just make the scraper general.
# So not to rewrite the whole thing for goldfish and then for something else.
# Who knows.

class Scraper:
    pass

def main():
    pass

if __name__ == "__main__":
    main()
