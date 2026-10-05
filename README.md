# ICE Certified Coffee Stocks ETL

End-to-end ETL pipeline for extracting, transforming, validating, and consolidating ICE Certified Coffee Stocks data for **Arabica and Robusta**.

## 1. Clone and Run

Clone the repository:

```bash
git clone https://github.com/khansadaf123/ice-coffee-stocks-etl.git
cd ice-coffee-stocks-etl
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the complete pipeline:

```bash
python run_pipeline.py
```

The pipeline processes:

- **Arabica — ICE Report 42**
- **Robusta — ICE Report 173**

The final consolidated dataset is generated as:

```text
Main_Final_data.csv
```

---

## 2. Project Objective

This project implements a reproducible ETL pipeline for ICE Certified Coffee Stocks data.

The pipeline:

- Extracts Arabica and Robusta reports.
- Preserves the extracted source data.
- Transforms both reports into a common analytical structure.
- Performs data quality and reconciliation checks.
- Produces one consolidated certified-stock dataset.
- Maintains source-file lineage.
- Supports incremental extraction.

---

## 3. Source Reports

| Coffee Type | ICE Report | Source Format |
|---|---|---|
| Arabica | Report 42 | XLS |
| Robusta | Report 173 | CSV |

The two reports have different source structures, so they are processed according to their respective source formats before being standardized.

---

## 4. Prerequisites

The project requires:

- Python 3.x
- Google Chrome
- Compatible ChromeDriver
- Internet access for source extraction

Python dependencies are listed in:

```text
requirements.txt
```

---

## 5. Configuration

[pipeline]

extraction = true
bronze     = true
silver     = true
final      = true


[extraction]

# true  -> re-scrape the full date range even if the data is present
force_extraction = false

# Pause after opening each ICE report page so a CAPTCHA can be
# completed in the Chrome window.
prompt_for_captcha = true

---

## 6. Medallion Architecture

The project follows a **Medallion Architecture**:

```text
Extraction
    ↓
Bronze
    ↓
Silver
    ↓
Final
```

### Extraction

Retrieves and stores the ICE source reports.

The extraction process supports incremental extraction, report-date tracking, pagination handling, download validation, and source-file inventory.

### Bronze

Structures the downloaded source reports while retaining source-specific information needed for traceability and downstream processing.

### Silver

Transforms Arabica and Robusta into a common analytical schema:

```text
coffee_type
report_date
location
origin
stock_category
stock_status
quantity
unit
source_file
```

### Final

Selects the required certified-stock records and creates the consolidated business output.

---

## 7. Repository Structure

```text
ice-coffee-stocks-etl/
│
├── README.md
├── ICE_Coffee_Stocks_Technical_Documentation.docx
├── requirements.txt
├── config.toml
├── run_pipeline.py
├── Main_Final_data.csv
│
├── extraction_layer/
├── bronze_layer/
├── silver_layer/
└── final_data_layer/
```

The extraction layer contains source reports and extraction inventories. Bronze and Silver contain intermediate datasets generated during processing.

---

## 8. Final Output

The final consolidated dataset is:

```text
Main_Final_data.csv
```

It contains certified stock records for both Arabica and Robusta.

### Final Data Dictionary

| Column | Description |
|---|---|
| `coffee_type` | Coffee type, Arabica or Robusta |
| `report_date` | ICE report date |
| `warehouse_location` | Warehouse/location associated with the stock |
| `origin` | Origin where provided by the source |
| `stock_category` | Certified stock category |
| `stock_status` | Stock status |
| `certified_stock_quantity` | Certified stock quantity |

The final dataset provides a common structure for both coffee types.

---

## 9. Data Quality and Validation

The pipeline performs validation at multiple stages.

Key checks include:

- Download completeness
- Report-date coverage
- Pagination completeness
- Duplicate detection
- Schema and column validation
- Source-to-transformed reconciliation
- Natural-key uniqueness
- Missing report dates
- Negative quantities
- Missing stock classifications
- Final certified-stock validation

Reconciliation checks help ensure that quantities are not lost or duplicated during transformation.

---

## 10. Reproducibility

The complete workflow can be executed from a fresh clone using:

```bash
git clone https://github.com/khansadaf123/ice-coffee-stocks-etl.git
cd ice-coffee-stocks-etl
pip install -r requirements.txt
python run_pipeline.py
```

`run_pipeline.py` is the main executable entry point for the complete ETL workflow.

Source files and intermediate datasets are retained as processing checkpoints, while source-file lineage is carried through the transformation process.

---

## 11. Technical Documentation

The detailed implementation is documented separately in:

```text
ICE_Coffee_Stocks_Technical_Documentation.docx
```

The technical documentation covers the detailed processing and validation logic across the Extraction, Bronze, Silver, and Final layers.
