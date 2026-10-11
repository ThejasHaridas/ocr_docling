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
    pan: Optional[str] = None
    dl_no: Optional[str] = None
    fssai: Optional[str] = None


class LineItem(BaseModel):
    """A single row from the invoice's line-item table.

    Superset of columns seen across templates (general trade + pharma). Fields
    that don't apply to a given invoice stay ``None``.
    """

    sln: Optional[str] = None
    group_item: Optional[str] = None
    description: Optional[str] = None
    hsn_code: Optional[str] = None
    mfr: Optional[str] = None
    pack: Optional[str] = None
    batch: Optional[str] = None
    expiry: Optional[str] = None
    mrp: Optional[float] = None
    rate: Optional[float] = None
    qty: Optional[float] = None
    free_qty: Optional[float] = None
    uom: Optional[str] = None
    discount_pct: Optional[float] = None
    gst_pct: Optional[float] = None
    gst_amount: Optional[float] = None
    value: Optional[float] = None
    # Extra columns used by the purchase-invoice mapping (see purchase.py).
    replace_qty: Optional[float] = None
    discount_amount: Optional[float] = None
    sch_discount_pct: Optional[float] = None
    cgst_amount: Optional[float] = None
    sgst_amount: Optional[float] = None
    cess_amount: Optional[float] = None
    net_amount: Optional[float] = None


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

    total_items: Optional[float] = None
    total_qty: Optional[float] = None
    taxable_amount: Optional[float] = None
    discount: Optional[float] = None
    sgst: Optional[float] = None
    cgst: Optional[float] = None
    igst: Optional[float] = None
    cess: Optional[float] = None
    tax_amount: Optional[float] = None
    round_off: Optional[float] = None
    grand_total: Optional[float] = None


class PurchaseLine(BaseModel):
    """One purchase-invoice line in the AI Pharma data model.

    Field names follow the "Our field" column of the Purchase Invoice column
    mapping. Quantities and prices are normalised to both pack and unit level
    (``packs`` x ``packSize`` = ``quantity``). Anything not printed on a supplier
    invoice (product master data, selling-price setup) is left out; see
    ``column_mapping.py`` for the full 70-column catalog.
    """

    sequenceNumber: Optional[int] = None
    itemName: Optional[str] = None
    manufacturer: Optional[str] = None
    hsnCode: Optional[str] = None
    drugForm: Optional[str] = None
    packLabel: Optional[str] = None
    packSize: Optional[int] = None

    batch: Optional[str] = None
    expiryDate: Optional[str] = None

    packs: Optional[float] = None
    freePacks: Optional[float] = None
    replacementPacks: Optional[float] = None
    quantity: Optional[float] = None
    freeQuantity: Optional[float] = None
    replacementQuantity: Optional[float] = None
    totalQuantity: Optional[float] = None
    scheme: Optional[str] = None

    mrpPerPack: Optional[float] = None
    mrp: Optional[float] = None
    ratePerPack: Optional[float] = None
    purchaseRate: Optional[float] = None

    grossAmount: Optional[float] = None
    discountPercent: Optional[float] = None
    discountAmount: Optional[float] = None
    specialDiscountPercent: Optional[float] = None
    specialDiscountAmount: Optional[float] = None
    purchaseAmount: Optional[float] = None

    taxPercent: Optional[float] = None
    cgstAmount: Optional[float] = None
    sgstAmount: Optional[float] = None
    cessAmount: Optional[float] = None
    taxAmount: Optional[float] = None
    billedAmount: Optional[float] = None

    landingCostPerPack: Optional[float] = None
    landingCostPerUnit: Optional[float] = None


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
    purchase_lines: List[PurchaseLine] = Field(default_factory=list)
    tax_summary: List[TaxLine] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)

    amount_in_words: Optional[str] = None
    challan_no: Optional[str] = None
    so_no: Optional[str] = None

    prepared_by: Optional[str] = None
    checked_by: Optional[str] = None
    delivered_by: Optional[str] = None

    raw: RawOutput = Field(default_factory=RawOutput)
