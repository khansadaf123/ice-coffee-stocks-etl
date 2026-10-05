"""
Coffee certified-stock pipeline - runs the medallion layers in order.

   1. Extraction Layer  extraction_layer/extraction.py   ICE reports -> raw XLS/CSV files
   2. Bronze Layer      bronze_layer/bronze.py           raw files   -> one table per coffee
   3. Silver Layer      silver_layer/silver.py           Bronze      -> cleaned, combined table
   4. Final Data Layer  final_data_layer/final_data.py   Silver      -> certified stock

Which layers run is set in the [pipeline] section of config.toml
(true = run, false = skip). A skipped layer's output files from an earlier
run are reused by the next layer. Each layer can also be run on its own.

Extraction only opens Chrome when config.toml asks for dates that haven't
been extracted yet, or when force_extraction is on.

Usage
-----
   python run_pipeline.py                     # the layers enabled in config.toml
   python run_pipeline.py --from bronze       # also skip every layer before bronze
   python run_pipeline.py --force-extraction  # re-scrape even if the data is present
   python run_pipeline.py --no-prompt         # don't pause for a CAPTCHA during extraction
"""

import argparse
import logging
import time
import tomllib
from pathlib import Path

from bronze_layer import bronze
from extraction_layer.extraction import run_extraction
from final_data_layer import final_data
from silver_layer import silver


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config.toml"

REPORTS = ["arabica", "robusta"]

LAYERS = ["extraction", "bronze", "silver", "final"]

# Files each layer reads - produced by the layer before it
LAYER_INPUTS = {
   "bronze": [
       (bronze.ARABICA_RAW_DIR, "*.xls"),
       (bronze.ROBUSTA_RAW_DIR, "*.csv"),
   ],
   "silver": [
       (silver.ARABICA_BRONZE_FILE, None),
       (silver.ROBUSTA_BRONZE_FILE, None),
   ],
   "final": [
       (final_data.SILVER_FILE, None),
   ],
}

log = logging.getLogger("pipeline")


def enabled_layers(start_layer="extraction"):
   """Layers switched on in config.toml [pipeline], from start_layer onwards."""

   with open(CONFIG_FILE, "rb") as f:
       switches = tomllib.load(f).get("pipeline", {})

   unknown = set(switches) - set(LAYERS)

   if unknown:
       raise ValueError(f"config.toml [pipeline]: unknown layers {sorted(unknown)}, expected {LAYERS}")

   not_bool = [layer for layer, value in switches.items() if not isinstance(value, bool)]

   if not_bool:
       raise ValueError(f"config.toml [pipeline]: {not_bool} must be true or false")

   return [
       layer for layer in LAYERS[LAYERS.index(start_layer):]
       if switches.get(layer, True)
   ]


def check_inputs_available(layers):
   """
   A layer whose previous layer is skipped needs that layer's output
   files from an earlier run. Fail before running anything if they're missing.
   """

   problems = []

   for layer in layers:

       previous = LAYERS[LAYERS.index(layer) - 1]

       if layer == "extraction" or previous in layers:
           continue

       for path, pattern in LAYER_INPUTS[layer]:

           exists = any(path.glob(pattern)) if pattern else path.exists()

           if not exists:
               problems.append(
                   f"'{layer}' needs {path.relative_to(PROJECT_DIR)}"
                   f"{'/' + pattern if pattern else ''}, which the skipped "
                   f"'{previous}' layer produces - set {previous} = true"
               )

   if problems:
       raise FileNotFoundError(
           "Cannot run the pipeline:\n  " + "\n  ".join(problems)
       )


def run_pipeline(start_layer="extraction", force_extraction=None, interactive=None):
   """force_extraction / interactive default to the config.toml settings."""

   layer_steps = {
       "extraction": lambda: run_extraction(REPORTS, force_extraction, interactive),
       "bronze": lambda: bronze.run_bronze(REPORTS),
       "silver": silver.run_silver,
       "final": final_data.run_final_data,
   }

   layers = enabled_layers(start_layer)
   skipped = [layer for layer in LAYERS if layer not in layers]

   if not layers:
       log.warning("No layers to run - every layer is false in config.toml [pipeline]")
       return

   check_inputs_available(layers)

   log.info("Running: %s | skipped: %s", " -> ".join(layers), ", ".join(skipped) or "none")

   pipeline_start = time.time()

   for layer in layers:

       layer_start = time.time()
       layer_steps[layer]()

       log.info("%s layer finished in %.1fs", layer.capitalize(), time.time() - layer_start)

   log.info("=" * 60)
   log.info("PIPELINE COMPLETE in %.1fs", time.time() - pipeline_start)
   log.info("=" * 60)


def main():

   parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
   parser.add_argument(
       "--from",
       dest="start_layer",
       choices=LAYERS,
       default="extraction",
       help="also skip every layer before this one (config.toml still applies to the rest)",
   )
   parser.add_argument(
       "--force-extraction",
       action="store_true",
       help="re-extract even if the data is already present (overrides config.toml)",
   )
   parser.add_argument(
       "--no-prompt",
       action="store_true",
       help="don't pause for a manual CAPTCHA after opening each ICE report page",
   )
   args = parser.parse_args()

   logging.basicConfig(
       level=logging.INFO,
       format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
       datefmt="%H:%M:%S",
   )

   run_pipeline(
       start_layer=args.start_layer,
       force_extraction=True if args.force_extraction else None,
       interactive=False if args.no_prompt else None,
   )


if __name__ == "__main__":
   main()

