"""Pydantic schema describing the structured output of a parsed bill/invoice.

Every field is optional and defaults to ``None`` (or an empty list) so that the
parser can degrade gracefully when OCR misses something instead of raising.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Party(BaseModel):
    """A company/person on the invoice (seller, bill-to or ship-to)."""

    name: Optional[str] = None
    address_lines: List[str] = Field(default_factory=list)
    city: Optional[str] = None
    state: Optional[str] = None
    state_code: Optional[str] = None
    pin: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None


class LineItem(BaseModel):
    """A single row from the invoice's line-item table."""

    sln: Optional[str] = None
    group_item: Optional[str] = None
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    mrp: Optional[float] = None
    rate: Optional[float] = None
    qty: Optional[float] = None
    uom: Optional[str] = None
    value: Optional[float] = None


class TaxLine(BaseModel):
    """A row from the HSN/SAC tax-summary table."""

    hsn_sac: Optional[str] = None
    taxable_amount: Optional[float] = None
    cgst_rate: Optional[float] = None
    cgst_amount: Optional[float] = None
    sgst_rate: Optional[float] = None
    sgst_amount: Optional[float] = None
    igst_rate: Optional[float] = None
    igst_amount: Optional[float] = None


class Totals(BaseModel):
    """Invoice-level totals."""

    total_qty: Optional[float] = None
    taxable_amount: Optional[float] = None
    sgst: Optional[float] = None
    cgst: Optional[float] = None
    igst: Optional[float] = None
    grand_total: Optional[float] = None


class RawOutput(BaseModel):
    """Raw Docling output retained as a safety net / for debugging."""

    markdown: Optional[str] = None
    tables: List[List[Dict[str, Any]]] = Field(default_factory=list)


class Invoice(BaseModel):
    """Top-level structured representation of a bill/invoice."""

    seller: Party = Field(default_factory=Party)
    bill_to: Party = Field(default_factory=Party)
    ship_to: Party = Field(default_factory=Party)

    invoice_no: Optional[str] = None
    invoice_date: Optional[str] = None

    transporter: Optional[str] = None
    destination: Optional[str] = None
    lr_no: Optional[str] = None
    agent: Optional[str] = None
    doc_no: Optional[str] = None
    packet_no: Optional[str] = None

    line_items: List[LineItem] = Field(default_factory=list)
    tax_summary: List[TaxLine] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)

    amount_in_words: Optional[str] = None
    challan_no: Optional[str] = None
    so_no: Optional[str] = None

    prepared_by: Optional[str] = None
    checked_by: Optional[str] = None
    delivered_by: Optional[str] = None

    raw: RawOutput = Field(default_factory=RawOutput)
