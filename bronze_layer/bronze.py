import argparse
import logging
import re
from pathlib import Path

import pandas as pd


# Configuration

BRONZE_DIR = Path(__file__).resolve().parent
EXTRACTION_DIR = BRONZE_DIR.parent / "extraction_layer"

ARABICA_RAW_DIR = EXTRACTION_DIR / "arabica_report_42" / "raw_files"
ROBUSTA_RAW_DIR = EXTRACTION_DIR / "robusta_report_173" / "raw_files"

ARABICA_BRONZE_FILE = BRONZE_DIR / "arabica_bronze_data.xlsx"
ROBUSTA_BRONZE_FILE = BRONZE_DIR / "robusta_bronze_data.xlsx"

log = logging.getLogger("bronze")


# Shared helpers

def normalize_text(value):
   """Cell value as a trimmed, upper-case string with single spaces."""

   if pd.isna(value):
       return ""

   return re.sub(r"\s+", " ", str(value)).strip().upper()


def read_raw_files(raw_dir, pattern, read_file):
   """
   Read every raw file in raw_dir with read_file, drop completely empty
   rows/columns and normalize the text.

   Returns a list of (filename, DataFrame). Raises if any file can't be read,
   so a missing report day never slips silently into the Bronze data.
   """

   paths = sorted(raw_dir.glob(pattern))
   print("Pattern",pattern)
   if not paths:
       raise FileNotFoundError(f"No {pattern} files found in {raw_dir}")

   reports = []
   failed = []

# Read and clean each raw report
   for path in paths:
       try:
           df = read_file(path)
       except Exception as e:
           failed.append(f"{path.name}: {e}")
           continue
        # Remove completely empty rows and columns
       df = df.dropna(how="all").dropna(axis=1, how="all")
       reports.append((path.name, df.map(normalize_text)))

   if failed:
       raise RuntimeError(
           f"{len(failed)} files in {raw_dir} could not be read:\n" + "\n".join(failed)
       )

   log.info("Read %d files from %s", len(reports), raw_dir)

   return reports

# Make sure no raw file was lost during processing
def check_every_file_contributed(bronze_df, reports):

   missing = sorted(
       {filename for filename, _ in reports} - set(bronze_df["source_file"])
   )

   if missing:
       raise RuntimeError(f"{len(missing)} files produced no Bronze rows: {missing[:5]}")

# Save Bronze data as Excel
def save_bronze(bronze_df, path):

   bronze_df.to_excel(path, index=False)

   log.info("Saved %s (%d rows, %d columns)", path.name, *bronze_df.shape)



ARABICA_TITLE = "CERTIFIED WAREHOUSE STOCK REPORT"
ARABICA_DATE_PATTERN = re.compile(r"AS OF:\s*([A-Z]{3} \d{1,2}, \d{4})")


def read_arabica_file(path):
   return pd.read_excel(path, header=None, engine="xlrd")


def non_empty_values(row):
   return [value for value in row if value]

# Extract coffee type and report date from the report header
def extract_arabica_metadata(rows):

   metadata = {"coffee_type": None, "report_date": None}

   for _, values in rows:

       text = values[0]

       if ARABICA_TITLE in text:
           metadata["coffee_type"] = text.replace(ARABICA_TITLE, "").strip()

       match = ARABICA_DATE_PATTERN.search(text)

       if text.startswith("AS OF:") and match:
           metadata["report_date"] = pd.to_datetime(
               match.group(1), format="%b %d, %Y"
           ).date()

   return metadata


def split_arabica_sections(rows):
   """Group rows into sections, each starting at a single-value heading row."""

   sections = []
   current = None

   for row_number, values in rows:

       if len(values) == 1:
           current = {"heading": values[0], "rows": []}
           sections.append(current)

       else:
           if current is None:
               current = {"heading": None, "rows": []}
               sections.append(current)

           current["rows"].append((row_number, values))


   return sections


def arabica_section_records(section, source_file, metadata):
   """Bronze records for one section, or [] if it isn't a table."""

   if len(section["rows"]) < 2:
       return []

   header = section["rows"][0][1]

   if len(header) < 2:
       return []

   records = []

   for row_number, values in section["rows"][1:]:

       # Origin followed by one value per header column
       if len(values) != len(header) + 1:
           continue

       record = {
           "source_file": source_file,
           "report_date": metadata["report_date"],
           "coffee_type": metadata["coffee_type"],
           "section": section["heading"],
           "source_row": row_number,
           "origin": values[0],
       }
       record.update(zip(header, values[1:]))

       records.append(record)

   return records


def build_arabica_bronze():

   reports = read_raw_files(ARABICA_RAW_DIR, "*.xls", read_arabica_file)

   records = []
# Parse each report into Bronze records
   for source_file, df in reports:

       rows = [
           (row_number, values)
           for row_number, values in enumerate(
               non_empty_values(row) for row in df.itertuples(index=False)
           )
           if values
       ]

       metadata = extract_arabica_metadata(rows)

       for section in split_arabica_sections(rows):
           records.extend(arabica_section_records(section, source_file, metadata))
# Combine all parsed records
   bronze_df = pd.DataFrame(records)

   check_every_file_contributed(bronze_df, reports)
   check_arabica_dates(bronze_df)

   log.info(
       "Arabica Bronze sections:\n%s",
       bronze_df["section"].value_counts().to_string(),
   )

   return bronze_df


def check_arabica_dates(bronze_df):
   """Every file needs exactly one report date, and every date one file."""

   if bronze_df["report_date"].isna().any():
       files = bronze_df.loc[bronze_df["report_date"].isna(), "source_file"].unique()
       raise RuntimeError(f"No 'AS OF' report date found in: {list(files)[:5]}")

   dates_per_file = bronze_df.groupby("source_file")["report_date"].nunique()
   files_per_date = bronze_df.groupby("report_date")["source_file"].nunique()

   if (dates_per_file > 1).any():
       raise RuntimeError(
           f"Files with several report dates: {list(dates_per_file[dates_per_file > 1].index)}"
       )

   if (files_per_date > 1).any():
       raise RuntimeError(
           f"Report dates with several files: {list(files_per_date[files_per_date > 1].index)}"
       )

   log.info(
       "Arabica report dates: %d, %s to %s",
       bronze_df["report_date"].nunique(),
       bronze_df["report_date"].min(),
       bronze_df["report_date"].max(),
   )


# Robusta (ICE Report 173)

def read_robusta_file(path):
   return pd.read_csv(path)

# Read and combine Robusta CSV reports
def build_robusta_bronze():

   reports = read_raw_files(ROBUSTA_RAW_DIR, "*.csv", read_robusta_file)

   bronze_df = pd.concat(
       [df.assign(source_file=source_file) for source_file, df in reports],
       ignore_index=True,
   )

   check_every_file_contributed(bronze_df, reports)

   return bronze_df


# Run Bronze layer
# Report-specific Bronze builders and output files
BUILDERS = {
   "arabica": (build_arabica_bronze, ARABICA_BRONZE_FILE),
   "robusta": (build_robusta_bronze, ROBUSTA_BRONZE_FILE),
}

# Build and save selected reports
def run_bronze(report_names):

   for name in report_names:

       log.info("=" * 60)
       log.info("BRONZE: %s", name)
       log.info("=" * 60)

       build, output_file = BUILDERS[name]
       save_bronze(build(), output_file)


def main():
    parser = argparse.ArgumentParser(description="Build Bronze datasets from the extracted Arabica and Robusta reports.")
    parser.add_argument(
       "--report",
       choices=["arabica", "robusta", "all"],
       default="all",
   )
    args = parser.parse_args()

    logging.basicConfig(
       level=logging.INFO,
       format="%(asctime)s %(levelname)s %(message)s",
       datefmt="%H:%M:%S",
    )

    run_bronze(list(BUILDERS) if args.report == "all" else [args.report])


if __name__ == "__main__":
   main()

