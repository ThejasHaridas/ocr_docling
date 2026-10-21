# bill_extractor

Parse photos/scans of **bills and invoices** into clean, structured **JSON**,
powered by [Docling](https://www.docling.ai/) for OCR and table-structure
recognition.

Docling does the document understanding (OCR + layout + TableFormer table
extraction); a tolerant rule-based layer on top maps the recognised text and
tables into a typed invoice schema. Everything runs **locally** — no API keys
required.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> **First run downloads models.** Docling pulls its layout + table-structure
> models (from Hugging Face) and its OCR models on first use, so the first
> extraction is slow and needs network access. Subsequent runs are offline.

### Restricted / air-gapped networks

The models live on Hugging Face (and the default RapidOCR engine on ModelScope).
If those hosts are blocked, pre-download the models somewhere with access and
point the extractor at them:

```bash
# On a machine where huggingface.co is reachable:
docling-tools models download -o ./docling-models

# Then, anywhere:
export BILL_EXTRACTOR_ARTIFACTS=./docling-models
python -m bill_extractor examples/sample_invoice.jpg
```

Environment variables:

| Variable | Purpose |
|----------|---------|
| `BILL_EXTRACTOR_BACKEND` | `auto` (default: Docling, falling back to text if models are blocked), `docling` (force Docling), or `text` (pypdfium2 text extraction — no models, digital PDFs only) |
| `BILL_EXTRACTOR_OCR` | OCR engine: `auto` (default), `rapidocr`, `easyocr`, `tesseract`, `tesseract_cli` |
| `BILL_EXTRACTOR_ARTIFACTS` | Path to a local Docling model cache (offline use) |

If models can't be fetched, the CLI exits with a clear, actionable message
instead of a stack trace.

### Offline text backend (digital PDFs)

For **digital / text-based PDFs** (not scans), you can skip Docling's models
entirely and extract with the built-in pypdfium2 text backend — fully offline:

```bash
BILL_EXTRACTOR_BACKEND=text python -m bill_extractor invoice.pdf
```

In `auto` mode this also kicks in automatically as a fallback when Docling's
models can't be downloaded. For scanned images you still need Docling (OCR).

## CLI usage

```bash
# Single image -> JSON on stdout
python -m bill_extractor examples/sample_invoice.jpg

# Single image -> JSON file
python -m bill_extractor examples/sample_invoice.jpg -o invoice.json

# A folder of bills -> one <name>.json per image
python -m bill_extractor ./bills -o ./out

# Compact (single-line) JSON
python -m bill_extractor examples/sample_invoice.jpg --compact
```

Supported inputs: `.jpg .jpeg .png .bmp .tif .tiff .webp .pdf`.

## Library usage

```python
from bill_extractor import extract_bill, extract_bill_json

invoice = extract_bill("examples/sample_invoice.jpg")  # -> Invoice (pydantic)
print(invoice.invoice_no, invoice.totals.grand_total)
for item in invoice.line_items:
    print(item.hsn_code, item.qty, item.value)

# Or straight to a JSON string
print(extract_bill_json("examples/sample_invoice.jpg", pretty=True))
```

## Output schema

The top-level `Invoice` object contains:

| Field | Description |
|-------|-------------|
| `seller` | Seller `Party` (name, address, GSTIN, phone, email, state code) |
| `bill_to`, `ship_to` | Buyer / consignee `Party` blocks |
| `invoice_no`, `invoice_date` | Invoice identifiers |
| `transporter`, `destination`, `lr_no`, `agent`, `doc_no`, `packet_no` | Logistics metadata |
| `line_items[]` | `LineItem`: sln, description, hsn_code, mfr, pack, batch, expiry, mrp, rate, qty, free_qty, replace_qty, discount, gst, value (taxable), net_amount, ... |
| `purchase_lines[]` | `PurchaseLine`: each line normalised to the AI Pharma purchase-invoice model (see below) |
| `tax_summary[]` | `TaxLine`: hsn_sac, taxable_amount, cgst/sgst/igst rate & amount |
| `totals` | `Totals`: total_qty, taxable_amount, sgst, cgst, igst, grand_total |
| `amount_in_words`, `challan_no`, `so_no` | Misc invoice fields |
| `prepared_by`, `checked_by`, `delivered_by` | Signatories |
| `raw` | Raw Docling output (`markdown` + extracted `tables`) — a safety net for anything the heuristics miss |

Every field is optional and degrades to `null` when OCR can't recover it, so the
extractor never fails on an imperfect scan. When a field looks wrong, the
`raw.markdown` / `raw.tables` payload lets you recover or re-map it.

## Purchase-invoice column mapping

`Purchase_Invoice_Column_Mapping.pdf` maps 70 EqSoft / AI Pharma line-item
columns onto our data model. The pipeline implements it in two layers:

1. **Docling table -> `LineItem`**: `parser.parse_line_items` resolves each
   table header through a synonym map (`PKG.`/`Packing`, `EX.DT`/`Exp`/`Expiry`,
   `M.R.P`, `Net Amt.`/`Total`, ...), so the different supplier layouts (Chem
   Pharma, Fatima group, Bluefox, Kurikkal, KIMS, ...) land in the same fields.
   Scheme stock printed as a second `Free` row is merged into `free_qty`.
2. **`LineItem` -> `PurchaseLine`** (`purchase.py`): normalises pack vs unit
   figures using the pack size parsed from the pack label (`10's` -> 10,
   `100ML`/`30Gr` -> 1):

   | Field | Rule |
   |-------|------|
   | `quantity` / `freeQuantity` / `totalQuantity` | packs x `packSize` |
   | `mrp`, `purchaseRate` | `mrpPerPack`, `ratePerPack` / `packSize` |
   | `grossAmount` | packs x rate; `discountAmount` printed or implied by gross vs amount |
   | `cgstAmount` / `sgstAmount` | printed, else tax / 2 (intra-state) |
   | `landingCostPerPack` / `PerUnit` | `purchaseAmount` / (packs + free + replacement), then / `packSize` |
   | `scheme` | `"<packs> + <free>"` e.g. `"9 + 1"` |

`bill_extractor.column_mapping.COLUMNS` holds the full 70-row catalog with each
column's source: `invoice`, `derived`, `master` (barcode, composition, brand,
schedule... resolved downstream from `itemName`/`hsnCode`) or `setup`
(godown, price levels, margins - never on a supplier invoice).
`column_mapping.OCR_HEADERS` lists the invoice header spellings from the mapping
sheet's *OCR* column (`DESCRIPTION`/`Products`/`Item Description`, `EX.DT`/`Expiry
Date`, `DIS%`/`DISC %`, `GST%`/`TAX %`, `Net Amt`/`Net Amount`/`grand total`, ...);
the tests check that every one of them is extracted.
`column_mapping.coverage(invoice.purchase_lines)` reports how many lines filled
each targeted column. Run the tests with `python -m unittest discover tests`.

## Supported templates & examples

The rule-based layer is designed to generalise across invoice layouts. The
`examples/` folder ships several real templates and their extracted JSON:

| Input | Type | Output |
|-------|------|--------|
| `sample_invoice.jpg` | Raajaram & Co (general trade, image) | `sample_output.json` |
| `chempharma_invoice.pdf` | Chem Pharma (pharma distributor, digital PDF) | `chempharma_invoice.json` |
| `fatima_invoice.pdf` | Fatima Drug Lines (pharma distributor, digital PDF) | `fatima_invoice.json` |
| `unicorn_invoice.pdf` | Unicorn Enterprises (pharma distributor, digital PDF) | `unicorn_invoice.json` |
| `fatima_house_invoice.jpg` | Fatima Drug House, BlueFox software (pharma distributor, photo) | `fatima_house_invoice.json` (from the Docling-shaped fixture in `tests/fixture_fatima_house.py`) |

Regenerate them with:

```bash
# image template (uses the reconstructed Docling output helper, see below)
python examples/generate_sample_output.py
# digital PDFs via the offline text backend
BILL_EXTRACTOR_BACKEND=text python -m bill_extractor examples/chempharma_invoice.pdf -o examples/chempharma_invoice.json
```

Across these templates the extractor reliably pulls seller identity (name /
GSTIN / PAN), invoice number & date, line items (incl. pharma fields: MFR, HSN,
batch, expiry, MRP, qty, rate, GST% & amount), the GST tax summary, totals
(taxable, discount, round-off, grand total) and the amount in words.

### Known limitations

- **Line items need a clean row structure.** Docling's table recognition (or a
  one-line-per-item layout like the Chem Pharma / Fatima PDFs) yields full line
  items. On layouts where the raw text stream interleaves columns across lines
  (e.g. the Unicorn PDF via the *text* backend), per-row items can't be
  reconstructed — running through **Docling** (with model access) recovers them
  via layout analysis. Header fields, tax and totals are still extracted.
- **Party-block attribution is best-effort.** Which block is buyer vs. consignee
  is inferred heuristically (and by excluding the seller's GSTIN); on dense
  multi-block layouts this can be approximate. The raw Docling/text output is
  retained under `raw` so anything mis-attributed can be recovered.

`sample_output.json` is produced by
[`examples/generate_sample_output.py`](examples/generate_sample_output.py),
which runs the real parser over a faithful reconstruction of Docling's output,
so the repo ships a representative image-template example even where the model
hosts are blocked. With Hugging Face reachable, `python -m bill_extractor
examples/sample_invoice.jpg` yields the same structure directly.
