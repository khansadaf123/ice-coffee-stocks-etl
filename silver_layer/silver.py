import logging
import re
from pathlib import Path

import pandas as pd


# Configuration

SILVER_DIR = Path(__file__).resolve().parent
BRONZE_DIR = SILVER_DIR.parent / "bronze_layer"

ARABICA_BRONZE_FILE = BRONZE_DIR / "arabica_bronze_data.xlsx"
ROBUSTA_BRONZE_FILE = BRONZE_DIR / "robusta_bronze_data.xlsx"

SILVER_FILE = SILVER_DIR / "silver_coffee_data.xlsx"
# Final Silver schema
SILVER_COLUMNS = [
   "coffee_type",
   "report_date",
   "location",
   "origin",
   "stock_category",
   "stock_status",
   "quantity",
   "unit",
   "source_file",
]

# Natural key of a Silver row - must be unique
SILVER_KEY = [
   "coffee_type",
   "report_date",
   "location",
   "origin",
   "stock_category",
   "stock_status",
   "unit",
]

log = logging.getLogger("silver")


# Shared helpers

# Standardize source column names
def standardize_column_names(df):
   """CamelCase / 'HA/BR' style names -> snake_case ('cut_off_date', 'ha_br')."""

   def snake_case(column):
       column = str(column).strip()
       column = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", column)
       column = re.sub(r"[^a-zA-Z0-9]+", "_", column)
       return column.lower().strip("_")

   return df.rename(columns=snake_case)

# Separate report totals from analytical rows

def split_total_rows(df, column, total_label):
   """Return (analytical rows, report total rows)."""

   is_total = df[column] == total_label

   log.info("%s rows: %d, analytical rows: %d", total_label, is_total.sum(), (~is_total).sum())

   return df[~is_total].copy(), df[is_total].copy()


# Validate and convert quantity columns

def to_integer(df, columns):
   """Cast quantity columns to nullable Int64, refusing to drop decimals."""

   for column in columns:

       values = df[column]
       fractional = values.notna() & (values % 1 != 0)

       if fractional.any():
           raise ValueError(f"{column}: {fractional.sum()} non-integer quantities")

       df[column] = values.astype("Int64")

   return df

# Map source fields to standard stock labels

def add_stock_labels(df, source_column, labels):
   """
   Set stock_category / stock_status from a {source value: (category, status)}
   mapping. Fails on any source value missing from the mapping.
   """

   unmapped = set(df[source_column].dropna()) - set(labels)

   if unmapped:
       raise ValueError(f"No stock label for {source_column} values: {sorted(unmapped)}")

   mapped = df[source_column].map(labels)

   df["stock_category"] = mapped.str[0]
   df["stock_status"] = mapped.str[1]

   return df

# Check transformed totals against source totals

def check_reconciliation(df, calculated, expected, label):
   """The reshaped quantities must add up to the report's own totals."""

   mismatches = df[df[calculated] != df[expected]]

   if len(mismatches):
       raise ValueError(
           f"{label}: {len(mismatches)} reconciliation mismatches\n"
           f"{mismatches.head(10).to_string()}"
       )

   log.info("%s reconciliation: %d groups, 0 mismatches", label, len(df))


def to_silver_columns(df):

   df = df[SILVER_COLUMNS].copy()
   df["origin"] = df["origin"].astype("string")

   return df


# Arabica transformation

# Columns kept from the original Bronze record

ARABICA_METADATA_COLUMNS = [
   "source_file",
   "report_date",
   "coffee_type",
   "section",
   "source_row",
   "origin",
]

# Map report sections to stock category/status

ARABICA_STOCK_LABELS = {
   "TOTAL BAGS CERTIFIED": ("total_bags_certified", None),
   "TRANSITION BAGS CERTIFIED": ("transition_bags_certified", None),
   "PENDING GRADING REPORT": (None, "pending_grading"),
   "BAGS PASSED GRADING": (None, "passed_grading"),
   "BAGS FAILED GRADING": (None, "failed_grading"),
   "FLAGGED FOR REBAGGING": (None, "flagged_for_rebagging"),
}


def build_arabica_silver():

   df = pd.read_excel(ARABICA_BRONZE_FILE)
   df = standardize_column_names(df)
# Convert source coffee code to business name

   df["coffee_type"] = df["coffee_type"].str.replace('COFFEE "C"', "Arabica", regex=False)

   df, _ = split_total_rows(df, "origin", "TOTAL IN BAGS")

# Warehouse columns become rows in Silver

   location_columns = [
       column for column in df.columns
       if column not in ARABICA_METADATA_COLUMNS + ["total"]
   ]

   log.info("Arabica location columns: %s", location_columns)
# Unpivot warehouse columns into location rows
   df = df.melt(
       id_vars=ARABICA_METADATA_COLUMNS + ["total"],
       value_vars=location_columns,
       var_name="location",
       value_name="quantity",
   )

   df = to_integer(df, ["quantity"])
   df = add_stock_labels(df, "section", ARABICA_STOCK_LABELS)
   df["unit"] = "bags"

# Each origin row's location quantities must add up to its TOTAL column
   reconciliation = (
       df.groupby(
           ["source_file", "report_date", "section", "source_row", "origin"],
           dropna=False,
       )
       .agg(source_total=("total", "first"), location_quantity=("quantity", "sum"))
       .reset_index()
   )

   check_reconciliation(reconciliation, "location_quantity", "source_total", "Arabica")

   return to_silver_columns(df)


# Robusta transformation

# Quantity fields to unpivot
ROBUSTA_QUANTITY_COLUMNS = [
   "lots_with_val_cert",
   "lots_non_tend",
   "lots_suspended",
]

# Map quantity fields to stock category/status
ROBUSTA_STOCK_LABELS = {
   "lots_with_val_cert": ("with_val_cert", "tenderable"),
   "lots_non_tend": ("non_tend", "non_tenderable"),
   "lots_suspended": ("suspended", "suspended"),
}


def build_robusta_silver():

   df = pd.read_excel(ROBUSTA_BRONZE_FILE)

   df["CutOffDate"] = pd.to_datetime(df["CutOffDate"], format="mixed")
   df = standardize_column_names(df)
# Remove report total rows before reshaping
   df, totals = split_total_rows(df, "commodity", "GRANDTOTAL")

   df = to_integer(df, ROBUSTA_QUANTITY_COLUMNS)
# Unpivot stock categories into rows
   df = df.melt(
       id_vars=["commodity", "cut_off_date", "port_id", "source_file"],
       value_vars=ROBUSTA_QUANTITY_COLUMNS,
       var_name="stock_status",
       value_name="quantity",
   )

   df = df.rename(columns={
       "commodity": "coffee_type",
       "cut_off_date": "report_date",
       "port_id": "location",
   })

   df["coffee_type"] = df["coffee_type"].replace("RC", "Robusta")

   df = add_stock_labels(df, "stock_status", ROBUSTA_STOCK_LABELS)
   # Robusta source does not provide origin
   df["origin"] = pd.NA
   df["unit"] = "lots"

   # Each report's port quantities must add up to its GRANDTOTAL row
   calculated_totals = (
       df.groupby(["source_file", "stock_category"], dropna=False)
       .agg(calculated_total=("quantity", "sum"))
       .reset_index()
   )

   source_totals = totals.melt(
       id_vars=["source_file"],
       value_vars=ROBUSTA_QUANTITY_COLUMNS,
       var_name="source_category",
       value_name="source_total",
   )
   source_totals["stock_category"] = (
       source_totals["source_category"].map(ROBUSTA_STOCK_LABELS).str[0]
   )

   reconciliation = calculated_totals.merge(
       source_totals[["source_file", "stock_category", "source_total"]],
       on=["source_file", "stock_category"],
       how="outer",
   )

   check_reconciliation(reconciliation, "calculated_total", "source_total", "Robusta")

   return to_silver_columns(df)


# Combine data and run quality checks

def check_silver_quality(silver_df):

   issues = []

# Check the Silver natural key
   duplicates = silver_df.duplicated(subset=SILVER_KEY).sum()
   if duplicates:
       issues.append(f"{duplicates} duplicate rows on {SILVER_KEY}")

# Quantities should never be negative
   negatives = (silver_df["quantity"] < 0).sum()
   if negatives:
       issues.append(f"{negatives} negative quantities")

# Every Silver row should have a report date
   null_dates = silver_df["report_date"].isna().sum()
   if null_dates:
       issues.append(f"{null_dates} rows without report_date")

# Every row should have a stock classification
   no_label = (silver_df["stock_category"].isna() & silver_df["stock_status"].isna()).sum()
   if no_label:
       issues.append(f"{no_label} rows with neither stock_category nor stock_status")

   if issues:
       raise ValueError("Silver quality checks failed:\n" + "\n".join(issues))

   log.info(
       "Silver quality checks passed. Rows by coffee:\n%s",
       silver_df["coffee_type"].value_counts().to_string(),
   )
   log.info(
       "Date range: %s to %s",
       silver_df["report_date"].min().date(),
       silver_df["report_date"].max().date(),
   )
   log.info("Null quantities: %d", silver_df["quantity"].isna().sum())


def build_silver():
# Combine Arabica and Robusta into one Silver dataset
   silver_df = pd.concat(
       [build_arabica_silver(), build_robusta_silver()],
       ignore_index=True,
   )

   check_silver_quality(silver_df)

   return silver_df


def run_silver():

   log.info("=" * 60)
   log.info("SILVER")
   log.info("=" * 60)
# Build and save the Silver dataset
   silver_df = build_silver()
   silver_df.to_excel(SILVER_FILE, index=False)

   log.info("Saved %s (%d rows, %d columns)", SILVER_FILE.name, *silver_df.shape)

   return silver_df


def main():

   logging.basicConfig(
       level=logging.INFO,
       format="%(asctime)s %(levelname)s %(message)s",
       datefmt="%H:%M:%S",
   )

   run_silver()


if __name__ == "__main__":
   main()

