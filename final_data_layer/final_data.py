import logging
from pathlib import Path

import pandas as pd


# Configuration

FINAL_DIR = Path(__file__).resolve().parent
SILVER_FILE = FINAL_DIR.parent / "silver_layer" / "silver_coffee_data.xlsx"

FINAL_FILE = FINAL_DIR.parent / "Main_Final_data.csv"

# Stock categories to keep in the final dataset
CERTIFIED_STOCK_CATEGORY = {
   "Arabica": "total_bags_certified",
   "Robusta": "with_val_cert",
}

# Rename columns for the final output
FINAL_COLUMN_NAMES = {
   "location": "warehouse_location",
   "quantity": "certified_stock_quantity",
}

log = logging.getLogger("final_data")


# Build final dataset

def build_final_data():

   silver_df = pd.read_excel(SILVER_FILE)

   final_df = pd.concat(
       [
           silver_df[
               (silver_df["coffee_type"] == coffee_type)
               & (silver_df["stock_category"] == stock_category)
           ]
           for coffee_type, stock_category in CERTIFIED_STOCK_CATEGORY.items()
       ],
       ignore_index=True,
   )

   final_df = final_df.rename(columns=FINAL_COLUMN_NAMES)

   # Excel turns the nullable integer quantities into floats
   final_df["certified_stock_quantity"] = (
       final_df["certified_stock_quantity"].astype("Int64")
   )

   check_final_data(final_df)
   final_df.drop(["source_file","unit"],axis=1,inplace=True)

   return final_df


def check_final_data(final_df):

   missing = set(CERTIFIED_STOCK_CATEGORY) - set(final_df["coffee_type"])

   if missing:
       raise ValueError(f"No certified stock rows for: {sorted(missing)}")

   null_quantities = final_df["certified_stock_quantity"].isna().sum()

   if null_quantities:
       raise ValueError(f"{null_quantities} rows without certified_stock_quantity")

   summary = final_df.groupby(["coffee_type", "unit"]).agg(
       rows=("certified_stock_quantity", "size"),
       report_dates=("report_date", "nunique"),
       first_date=("report_date", "min"),
       last_date=("report_date", "max"),
   )

   log.info("Final data summary:\n%s", summary.to_string())


def run_final_data():

   log.info("=" * 60)
   log.info("FINAL DATA")
   log.info("=" * 60)

   final_df = build_final_data()
   final_df.to_csv(FINAL_FILE, index=False)

   log.info("Saved %s (%d rows, %d columns)", FINAL_FILE.name, *final_df.shape)

   return final_df


def main():

   logging.basicConfig(
       level=logging.INFO,
       format="%(asctime)s %(levelname)s %(message)s",
       datefmt="%H:%M:%S",
   )
   run_final_data()


if __name__ == "__main__":
   main()

