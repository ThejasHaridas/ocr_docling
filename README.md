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
| `BILL_EXTRACTOR_OCR` | OCR engine: `auto` (default), `rapidocr`, `easyocr`, `tesseract`, `tesseract_cli` |
| `BILL_EXTRACTOR_ARTIFACTS` | Path to a local Docling model cache (offline use) |

If models can't be fetched, the CLI exits with a clear, actionable message
instead of a stack trace.

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
| `line_items[]` | `LineItem`: sln, group_item, description, hsn_code, mrp, rate, qty, uom, value |
| `tax_summary[]` | `TaxLine`: hsn_sac, taxable_amount, cgst/sgst/igst rate & amount |
| `totals` | `Totals`: total_qty, taxable_amount, sgst, cgst, igst, grand_total |
| `amount_in_words`, `challan_no`, `so_no` | Misc invoice fields |
| `prepared_by`, `checked_by`, `delivered_by` | Signatories |
| `raw` | Raw Docling output (`markdown` + extracted `tables`) — a safety net for anything the heuristics miss |

Every field is optional and degrades to `null` when OCR can't recover it, so the
extractor never fails on an imperfect scan. When a field looks wrong, the
`raw.markdown` / `raw.tables` payload lets you recover or re-map it.

See [`examples/sample_output.json`](examples/sample_output.json) for a full
example for `examples/sample_invoice.jpg`. (That file is produced by
[`examples/generate_sample_output.py`](examples/generate_sample_output.py),
which runs the real parser over a faithful reconstruction of Docling's output,
so the repo ships a representative example even where the model hosts are
blocked. With Hugging Face reachable, `python -m bill_extractor
examples/sample_invoice.jpg` yields the same structure directly.)
