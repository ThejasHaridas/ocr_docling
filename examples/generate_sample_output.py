"""Generate examples/sample_output.json for the bundled sample invoice.

Normally you would simply run:

    python -m bill_extractor examples/sample_invoice.jpg -o examples/sample_output.json

That performs the real Docling OCR + table extraction. This helper exists only
so the repository can ship a representative ``sample_output.json`` in CI/sandbox
environments where Docling's model hosts (Hugging Face / ModelScope) are not
reachable. It feeds the *same* rule-based parser a faithful reconstruction of
the markdown + tables Docling produces for examples/sample_invoice.jpg, so the
output matches what the live pipeline yields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bill_extractor.parser import parse

MARKDOWN = """RAAJARAM & CO
Kuthampully
Thrissur, Kerala - 680594, India
GSTIN: 32ABKFR5951J1ZV
Contact: 04884 209990 | +91 99477 99887
Email: info@raajaram.com
Tax Invoice
Date : 22-08-2025
Invoice No : RR-25-26-00306
Bill To : Soubhagya Silks
627
Edakkara,
Malappuram
Kerala, State Code: 32
PIN Code: 679331
India
Phone: 6282772085
GSTIN: 32ARBPJ2182L1ZX
Ship To : Soubhagya Silks
627
Edakkara,
Malappuram
Kerala, State Code: 32
PIN Code: 679331
India
Phone: 6282772085
GSTIN: 32ARBPJ2182L1ZX
Transporter : KRS
Destination : Edakkara
LR No :
Agent :
Doc No : 1
Packet No :
Total 24 10,560.00
State GST (SGST) [ @ 2.500% on 10560.00 ] 264.00
Central GST (CGST) [ @ 2.500% on 10560.00 ] 264.00
Grand Total 11,088.00
Amount In Words : INR Eleven Thousand And Eighty Eight only.
Challan No : DN-25-00318
SO no : RO-25-26-00310
Prepared By : Arun.R
Checked By : VISHNU
Delivered By : VISHNU
"""

_ROWS = [
    ("1", "MAJOR-HS M", "62052000", "685.00", "440.00", "2", "Nos", "880.00"),
    ("2", "MAJOR-HS L", "62052000", "685.00", "440.00", "2", "Nos", "880.00"),
    ("3", "MAJOR-HS XL", "62052000", "685.00", "440.00", "2", "Nos", "880.00"),
    ("4", "MAJOR-HS XXL", "62052000", "685.00", "440.00", "3", "Nos", "1,320.00"),
    ("5", "MAJOR-FS M", "62052000", "685.00", "440.00", "5", "Nos", "2,200.00"),
    ("6", "MAJOR-FS L", "62052000", "685.00", "440.00", "5", "Nos", "2,200.00"),
    ("7", "MAJOR-FS XL", "62052000", "685.00", "440.00", "3", "Nos", "1,320.00"),
    ("8", "MAJOR-FS XXL", "62052000", "685.00", "440.00", "3", "Nos", "1,320.00"),
]

COLS = ["SLN", "Group/Item", "HSN Code", "MRP", "Rate", "Qty", "UOM", "Value"]
LINE_TABLE = [dict(zip(COLS, r)) for r in _ROWS]
TAX_TABLE = [
    {
        "HSN/SAC": "62052000",
        "Taxable Amount": "10,560.00",
        "CGST": "(2.5%) 264.00",
        "SGST": "(2.5%) 264.00",
    }
]


def main() -> None:
    invoice = parse(MARKDOWN, [LINE_TABLE, TAX_TABLE])
    out = Path(__file__).with_name("sample_output.json")
    out.write_text(invoice.model_dump_json(indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
