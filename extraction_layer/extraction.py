import argparse
import json
import logging
import re
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


# Configuration

EXTRACTION_DIR = Path(__file__).resolve().parent
CONFIG_FILE = EXTRACTION_DIR.parent / "config.toml"

ICE_URL = "https://www.ice.com"

PAGE_LOAD_SECONDS = 5
DOWNLOAD_TIMEOUT_SECONDS = 60

ONE_DAY = timedelta(days=1)

log = logging.getLogger("extraction")


@dataclass(frozen=True)
class ReportConfig:
   name: str
   report_id: int
   file_extension: str
   pause_between_downloads: float

   @property
   def report_url(self):
       return f"{ICE_URL}/report/{self.report_id}"

   @property
   def output_dir(self):
       return EXTRACTION_DIR / f"{self.name}_report_{self.report_id}"

   @property
   def download_dir(self):
       return self.output_dir / "raw_files"

   @property
   def inventory_file(self):
       return self.output_dir / f"ice_report_{self.report_id}_links.json"

   @property
   def manifest_file(self):
       return self.output_dir / "extraction_manifest.json"

# Report-specific settings
ARABICA = ReportConfig(
   name="arabica",
   report_id=42,
   file_extension=".xls",
   pause_between_downloads=3,
)

ROBUSTA = ReportConfig(
   name="robusta",
   report_id=173,
   file_extension=".csv",
   pause_between_downloads=0,
)


def load_config():

   with open(CONFIG_FILE, "rb") as f:
       return tomllib.load(f)["extraction"]


def to_date(value):
   """date from a TOML date or a 'YYYY-MM-DD' string."""

   return value if isinstance(value, date) else date.fromisoformat(str(value))


# Track previously extracted date ranges

def merge_ranges(ranges):
   """Merge overlapping or adjacent (start, end) date ranges."""

   merged = []

   for start, end in sorted(ranges):

       if merged and start <= merged[-1][1] + ONE_DAY:
           merged[-1] = (merged[-1][0], max(merged[-1][1], end))
       else:
           merged.append((start, end))

   return merged


def missing_ranges(start, end, covered):
   """Parts of start..end that are not inside any covered range."""

   gaps = []
   cursor = start

   for covered_start, covered_end in merge_ranges(covered):

       if covered_end < cursor:
           continue

       if covered_start > end:
           break

       if covered_start > cursor:
           gaps.append((cursor, covered_start - ONE_DAY))

       cursor = covered_end + ONE_DAY

   if cursor <= end:
       gaps.append((cursor, end))

   return gaps


def format_ranges(ranges):
   return ", ".join(f"{start} to {end}" for start, end in ranges) or "none"


def load_inventory(config):

   if not config.inventory_file.exists():
       return []

   return json.loads(config.inventory_file.read_text())


def load_covered_ranges(config, inventory):
   """
   Date ranges already extracted. Read from the manifest; for data that
   was extracted before the manifest existed, the span of the link
   inventory is used instead.
   """

   if config.manifest_file.exists():

       manifest = json.loads(config.manifest_file.read_text())

       return [
           (to_date(start), to_date(end))
           for start, end in manifest["covered_ranges"]
       ]

   if inventory:

       dates = [to_date(link["report_date"]) for link in inventory]

       log.info(
           "Report %s: no manifest yet - treating the link inventory span "
           "%s to %s as extracted", config.report_id, min(dates), max(dates),
       )

       return [(min(dates), max(dates))]

   return []


def save_covered_ranges(config, ranges):

   manifest = {
       "covered_ranges": [[str(start), str(end)] for start, end in merge_ranges(ranges)],
       "updated_at": datetime.now().isoformat(timespec="seconds"),
   }

   config.manifest_file.write_text(json.dumps(manifest, indent=2))


def links_in_range(links, start, end):

   return [
       link for link in links
       if start <= to_date(link["report_date"]) <= end
   ]


def weekdays_without_report(links, start, end):
   """Weekdays in start..end with no report - usually exchange holidays."""

   report_dates = {to_date(link["report_date"]) for link in links}

   return [
       day.date()
       for day in pd.bdate_range(start, end)
       if day.date() not in report_dates
   ]


# Browser and download helpers

@contextmanager
def chrome_driver(download_dir):
   """Chrome session that saves downloads into download_dir."""

   download_dir.mkdir(parents=True, exist_ok=True)

   options = Options()
   options.add_experimental_option("prefs", {
       "download.default_directory": str(download_dir),
       "download.prompt_for_download": False,
       "download.directory_upgrade": True,
       "safebrowsing.enabled": True,
   })

   driver = webdriver.Chrome(options=options)

   try:
       yield driver
   finally:
       driver.quit()


def open_report_page(driver, url, interactive):
   """Open an ICE report page and give the user a chance to solve a CAPTCHA."""

   log.info("Opening %s", url)
   driver.get(url)
   time.sleep(PAGE_LOAD_SECONDS)

   if interactive:
       input("If ICE shows a CAPTCHA, complete it in Chrome, then press Enter... ")

   log.info("Page title: %s", driver.title)


def wait_for_download(path, timeout=DOWNLOAD_TIMEOUT_SECONDS):
   """Wait until Chrome has finished writing path."""

   partial = path.with_name(path.name + ".crdownload")

   for _ in range(timeout):
       time.sleep(1)

       if path.exists() and not partial.exists():
           return True

   return False

# Validate links before downloading
def validate_links(links, config, start, end):
   """Every report must have a unique URL, filename and date inside the range."""

   if not links:
       log.warning(
           "Report %s: ICE returned no reports for %s to %s",
           config.report_id, start, end,
       )
       return

   for field in ["download_url", "filename", "report_date"]:
       values = [link[field] for link in links]
       duplicates = len(values) - len(set(values))

       if duplicates:
           raise RuntimeError(
               f"Report {config.report_id}: {duplicates} duplicate {field} values."
           )

   dates = sorted(to_date(link["report_date"]) for link in links)

   if dates[0] < start or dates[-1] > end:
       raise RuntimeError(
           f"Report {config.report_id}: dates {dates[0]} to {dates[-1]} fall "
           f"outside {start} to {end}."
       )

   wrong_type = [
       link["filename"] for link in links
       if not link["filename"].lower().endswith(config.file_extension)
   ]

   if wrong_type:
       raise RuntimeError(
           f"Report {config.report_id}: unexpected file types: {wrong_type[:5]}"
       )

   log.info(
       "Report %s link validation passed: %d reports, %s to %s",
       config.report_id, len(links), dates[0], dates[-1],
   )

   no_report = weekdays_without_report(links, start, end)

   if no_report:
       log.info(
           "Report %s: %d weekdays without a report (holidays or not yet "
           "published): %s", config.report_id, len(no_report),
           ", ".join(map(str, no_report)),
       )


def merge_inventory(inventory, new_links):
   """Add new links to the inventory; a newly scraped date replaces the old one."""

   by_date = {link["report_date"]: link for link in inventory}
   by_date.update({link["report_date"]: link for link in new_links})

   return sorted(by_date.values(), key=lambda link: link["report_date"])


def save_link_inventory(links, config):
   """Checkpoint the collected links to JSON and Excel."""

   config.output_dir.mkdir(parents=True, exist_ok=True)

   links_df = pd.DataFrame(links)

   links_df.to_json(config.inventory_file, orient="records", indent=2)
   links_df.to_excel(config.inventory_file.with_suffix(".xlsx"), index=False)

   log.info("Saved link inventory: %s (%d reports)", config.inventory_file.name, len(links))


def download_reports(driver, links, config):
   """Download every report through Chrome, skipping files already present."""

   skipped = 0
   downloaded = 0
   failed = []

   for i, link in enumerate(links, start=1):

       path = config.download_dir / link["filename"]

       if path.exists():
           skipped += 1
           continue

       log.info("[%d/%d] Downloading %s", i, len(links), link["filename"])

       try:
           driver.get(link["download_url"])

           if not wait_for_download(path):
               error = "Download timeout"
           elif path.stat().st_size == 0:
               error = "Empty file"
           else:
               error = None

       except Exception as e:
           error = str(e)

       if error:
           log.warning("FAILED %s: %s", link["filename"], error)
           failed.append({**link, "error": error})

           # If the very first download fails the browser is most
           # likely blocked, so there is no point trying the rest.
           if downloaded == 0 and len(failed) == 1:
               raise RuntimeError(
                   f"First download failed ({error}). Check whether ICE "
                   "is showing a CAPTCHA or blocking the browser."
               )
       else:
           downloaded += 1

       time.sleep(config.pause_between_downloads)

   log.info(
       "Report %s downloads: %d new, %d already present, %d failed",
       config.report_id, downloaded, skipped, len(failed),
   )

   return failed

# Final download check
def check_download_completeness(links, config):
   """Every expected report file must be present in the download folder."""

   missing = sorted(
       link["filename"] for link in links
       if not (config.download_dir / link["filename"]).exists()
   )

   if missing:
       raise RuntimeError(
           f"Report {config.report_id}: {len(missing)} files missing, "
           f"e.g. {missing[:5]}. Re-run to retry."
       )

   log.info("Report %s: all %d files present", config.report_id, len(links))


# Arabica - ICE Report 42

ARABICA_FILE_PATTERN = re.compile(r"coffee_cert_stock_(\d{8})\.xls$", re.IGNORECASE)


def arabica_links_on_page(driver, page_number):

   page_links = []

   for anchor in driver.find_elements(By.TAG_NAME, "a"):

       href = anchor.get_attribute("href") or ""
       match = ARABICA_FILE_PATTERN.search(href)

       if match:
           page_links.append({
               "report_date": pd.to_datetime(match.group(1), format="%Y%m%d")
                                .strftime("%Y-%m-%d"),
               "filename": href.split("/")[-1],
               "download_url": href,
               "report_name": anchor.text.strip(),
               "page": page_number,
           })

   return page_links


def check_arabica_page_sizes(page_sizes):
   """
   Every page except the last must be full (same size as page 1) and the
   last can't be bigger - otherwise a page was read before it had
   finished rendering.
   """

   page_size = page_sizes[0]

   short_pages = [
       (page, size)
       for page, size in enumerate(page_sizes[:-1], start=1)
       if size != page_size
   ]

   if short_pages or page_sizes[-1] > page_size:
       raise RuntimeError(
           f"Inconsistent page sizes {page_sizes} (expected {page_size} per "
           "page) - a page probably hadn't finished loading. Re-run."
       )

# Collect links from all result pages
def collect_arabica_links(driver, config, start, end, interactive):

   open_report_page(
       driver,
       f"{config.report_url}?startDate={start}&endDate={end}",
       interactive,
   )

   links = []
   seen_urls = set()
   page_sizes = []
   page_number = 1

   while True:

       time.sleep(3)

       page_links = arabica_links_on_page(driver, page_number)
       page_urls = {link["download_url"] for link in page_links}

       if not page_links:

           if page_number == 1:
               return []

           raise RuntimeError(f"Page {page_number}: no XLS report links found.")

       if len(page_urls) != len(page_links):
           raise RuntimeError(f"Page {page_number}: duplicate URLs within page.")

       overlap = page_urls & seen_urls

       if overlap:
           raise RuntimeError(
               f"Page {page_number}: {len(overlap)} URLs overlap with previous "
               "pages - pagination did not advance."
           )

       links.extend(page_links)
       seen_urls |= page_urls
       page_sizes.append(len(page_links))

       log.info(
           "Page %d: %d reports (%d collected so far)",
           page_number, len(page_links), len(links),
       )

       next_page = driver.find_elements(
           By.XPATH, f"//a[normalize-space(text())='{page_number + 1}']"
       )

       if not next_page:
           break

       driver.execute_script("arguments[0].click();", next_page[0])
       page_number += 1
       time.sleep(PAGE_LOAD_SECONDS)

   check_arabica_page_sizes(page_sizes)

   return links


# Robusta - ICE Report 173

ROBUSTA_REPORT_TYPE = "Stock Figures"

# Expected CSV columns

ROBUSTA_CSV_COLUMNS = [
   "Commodity",
   "CutOffDate",
   "PortId",
   "LotsWithValCert",
   "LotsNonTend",
   "LotsSuspended",
]

# Fetch report results through the ICE API

FETCH_REPORT_RESULTS_JS = """
const [path, reportType, startDate, endDate, done] = arguments;

fetch(path, {
   method: 'POST',
   headers: {'Content-Type': 'application/x-www-form-urlencoded'},
   body: new URLSearchParams({reportType, startDate, endDate})
})
.then(async response => done({status: response.status, text: await response.text()}))
.catch(error => done({status: 0, text: String(error)}));
"""


def drop_duplicate_dates(links):
   """Keep the first report for each date (ICE occasionally republishes one)."""

   kept = {}

   for link in links:

       if link["report_date"] in kept:
           log.warning(
               "Duplicate report for %s: keeping %s, dropping %s",
               link["report_date"],
               kept[link["report_date"]]["filename"],
               link["filename"],
           )
       else:
           kept[link["report_date"]] = link

   return list(kept.values())


def collect_robusta_links(driver, config, start, end, interactive):

   open_report_page(driver, config.report_url, interactive)

   result = driver.execute_async_script(
       FETCH_REPORT_RESULTS_JS,
       f"/marketdata/api/reports/{config.report_id}/results",
       ROBUSTA_REPORT_TYPE,
       str(start),
       str(end),
   )

   if result["status"] != 200:
       raise RuntimeError(
           f"Report {config.report_id} API returned HTTP {result['status']}: "
           f"{result['text'][:500]}"
       )

   rows = json.loads(result["text"])["datasets"]["reports"]["rows"]

   links = []
   rows_without_csv = []

   for row in rows:

       row_links = [
           {
               "report_date": row["reportDate"],
               "filename": report["url"].split("/")[-1],
               "download_url": ICE_URL + report["url"],
           }
           for report in row.get("reportList", [])
           if report.get("label") == "Download CSV/Excel"
           and report.get("method") == "GET"
           and report.get("type") == "download"
           and report.get("url", "").lower().endswith(".csv")
       ]

       if not row_links:
           rows_without_csv.append(row["reportDate"])

       links.extend(row_links)

   log.info("Report rows returned: %d, CSV links: %d", len(rows), len(links))

   # Each report row the API returns should offer one CSV download
   if rows_without_csv:
       log.warning(
           "Report %s: %d report rows have no CSV download: %s",
           config.report_id, len(rows_without_csv), rows_without_csv,
       )

   return drop_duplicate_dates(links)


def validate_robusta_files(links, config):
   """Every CSV must have the expected columns and a single cut-off date."""

   issues = []

   for link in links:

       path = config.download_dir / link["filename"]
       df = pd.read_csv(path)

       if df.columns.tolist() != ROBUSTA_CSV_COLUMNS:
           issues.append(f"{path.name}: columns {df.columns.tolist()}")

       cut_off_dates = df["CutOffDate"].dropna().unique()

       if len(cut_off_dates) != 1:
           issues.append(f"{path.name}: cut-off dates {list(cut_off_dates)}")

   if issues:
       raise RuntimeError(
           f"Report {config.report_id}: {len(issues)} file issues:\n"
           + "\n".join(issues)
       )

   log.info("Report %s: all %d CSV files are valid", config.report_id, len(links))


# Run extraction for selected reports

EXTRACTORS = {
   "arabica": (ARABICA, collect_arabica_links, None),
   "robusta": (ROBUSTA, collect_robusta_links, validate_robusta_files),
}

# Decide what needs to be extracted
def ranges_to_extract(config, inventory, covered, start, end, force):
   """Date ranges that need scraping: uncovered dates plus missing files."""

   if force:
       log.info("Report %s: force_extraction is on - re-extracting %s to %s",
                config.report_id, start, end)
       return [(start, end)]

   gaps = missing_ranges(start, end, covered)

   missing_files = [
       to_date(link["report_date"])
       for link in links_in_range(inventory, start, end)
       if not (config.download_dir / link["filename"]).exists()
   ]

   if missing_files:
       log.warning("Report %s: %d raw files missing from %s - re-extracting their dates",
                   config.report_id, len(missing_files), config.download_dir)

   log.info("Report %s: requested %s to %s, already extracted: %s",
            config.report_id, start, end, format_ranges(covered))

   return merge_ranges(gaps + [(day, day) for day in missing_files])

# Run the extraction workflow
def extract_report(config, collect_links, validate_files, start, end, force, interactive):

   log.info("=" * 60)
   log.info("EXTRACTION: %s (ICE Report %s)", config.name, config.report_id)
   log.info("=" * 60)

   inventory = load_inventory(config)
   covered = load_covered_ranges(config, inventory)
   to_extract = ranges_to_extract(config, inventory, covered, start, end, force)

   if not to_extract:

       if not config.manifest_file.exists():
           save_covered_ranges(config, covered)

       log.info(
           "Report %s: all data for %s to %s is already extracted - skipping. "
           "Set force_extraction = true in config.toml to re-extract.",
           config.report_id, start, end,
       )
       return links_in_range(inventory, start, end)

   log.info("Report %s: extracting %s", config.report_id, format_ranges(to_extract))

   new_links = []
   newly_covered = []
   yesterday = date.today() - ONE_DAY

   with chrome_driver(config.download_dir) as driver:

       for i, (range_start, range_end) in enumerate(to_extract):

           # The CAPTCHA only needs solving once per browser session
           links = collect_links(driver, config, range_start, range_end, interactive and i == 0)
           validate_links(links, config, range_start, range_end)

           new_links.extend(links)

           # Only mark dates up to yesterday as covered, and an empty
           # result only if there were no weekdays to report on
           covered_end = min(range_end, yesterday)

           if range_start <= covered_end and (
               links or not len(pd.bdate_range(range_start, covered_end))
           ):
               newly_covered.append((range_start, covered_end))

       inventory = merge_inventory(inventory, new_links)
       save_link_inventory(inventory, config)

       download_reports(driver, new_links, config)

   check_download_completeness(new_links, config)

   if validate_files:
       validate_files(new_links, config)

   save_covered_ranges(config, covered + newly_covered)

   log.info("Report %s: extracted dates now cover %s", config.report_id,
            format_ranges(merge_ranges(covered + newly_covered)))

   return links_in_range(inventory, start, end)


def run_extraction(report_names, force=None, interactive=None):
   """
   Extract the given reports. force / interactive default to the
   force_extraction / prompt_for_captcha settings in config.toml.
   """

   settings = load_config()

   if force is None:
       force = settings.get("force_extraction", False)

   if interactive is None:
       interactive = settings.get("prompt_for_captcha", True) and sys.stdin.isatty()

   for name in report_names:

       config, collect_links, validate_files = EXTRACTORS[name]

       start = to_date(settings[name]["start_date"])
       end = to_date(settings[name]["end_date"])

       if start > end:
           raise ValueError(f"config.toml [extraction.{name}]: start_date is after end_date")

       extract_report(config, collect_links, validate_files, start, end, force, interactive)



def main():

   parser = argparse.ArgumentParser(description="Extract Arabica and Robusta ICE coffee stock reports"
    )
   parser.add_argument(
       "--report",
       choices=["arabica", "robusta", "all"],
       default="all",
   )
   parser.add_argument(
       "--force",
       action="store_true",
       help="re-extract even if the data is already present (overrides config.toml)",
   )
   parser.add_argument(
       "--no-prompt",
       action="store_true",
       help="don't pause for a manual CAPTCHA after opening each report page",
   )
   args = parser.parse_args()

   logging.basicConfig(
       level=logging.INFO,
       format="%(asctime)s %(levelname)s %(message)s",
       datefmt="%H:%M:%S",
   )

   run_extraction(
       list(EXTRACTORS) if args.report == "all" else [args.report],
       force=True if args.force else None,
       interactive=False if args.no_prompt else None,
   )


if __name__ == "__main__":
   main()
