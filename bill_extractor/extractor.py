"""Orchestration: image path -> Docling -> rule-based parse -> Invoice/JSON."""

from __future__ import annotations

from pathlib import Path

from .converter import to_docling_document
from .parser import parse
from .schema import Invoice


def extract_bill(source: str | Path) -> Invoice:
    """Extract a structured :class:`Invoice` from a bill image (or PDF)."""
    _document, markdown, tables = to_docling_document(source)
    return parse(markdown, tables)


def extract_bill_json(source: str | Path, *, pretty: bool = True) -> str:
    """Extract a bill and return it as a JSON string."""
    invoice = extract_bill(source)
    indent = 2 if pretty else None
    return invoice.model_dump_json(indent=indent)
