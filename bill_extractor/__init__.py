"""bill_extractor: parse bill/invoice images into structured JSON using Docling."""

from .extractor import extract_bill, extract_bill_json
from .schema import Invoice, LineItem, Party, TaxLine, Totals

__all__ = [
    "extract_bill",
    "extract_bill_json",
    "Invoice",
    "Party",
    "LineItem",
    "TaxLine",
    "Totals",
]

__version__ = "0.1.0"
