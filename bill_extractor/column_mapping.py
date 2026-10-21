"""Catalog of the 70 purchase-invoice line-item columns (EqSoft -> AI Pharma).

Transcribed from ``Purchase_Invoice_Column_Mapping.pdf``. Each row records where
the value comes from when we process a *supplier invoice*:

* ``invoice`` - printed on the invoice; read straight off the Docling table.
* ``derived`` - computed from invoice values (e.g. per-unit rate = rate / pack size).
* ``master``  - product-master data (barcode, composition, brand, schedule...).
  Not on a supplier invoice; resolve downstream by matching ``itemName``/``hsnCode``.
* ``setup``   - selling-price / stock configuration done after receipt
  (godown, price levels, margins). Never on the invoice.

``extracted_as`` names the :class:`~bill_extractor.schema.PurchaseLine` field the
extractor fills for the column (``None`` for ``master``/``setup`` columns).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

from .schema import PurchaseLine


class Column(NamedTuple):
    no: int
    group: str
    eqsoft: str
    ai_pharma: str
    our_field: str  # existing field in our data model ("" when missing)
    status: str  # Mapped | Partial | Missing  (per the mapping PDF)
    extracted_as: Optional[str]  # PurchaseLine field filled by the extractor
    source: str  # invoice | derived | master | setup


_I, _D, _M, _S = "invoice", "derived", "master", "setup"

COLUMNS: List[Column] = [c for c in (
    # --- Product details -----------------------------------------------
    Column(1, "Product details", "SI#", "SI No", "sequenceNumber", "Mapped", "sequenceNumber", _I),
    Column(2, "Product details", "Code", "Item Code", "productCode", "Mapped", None, _M),
    Column(3, "Product details", "Barcode (product master)", "Barcode", "barcode", "Mapped", None, _M),
    Column(4, "Product details", "Product", "Product", "itemName", "Mapped", "itemName", _I),
    Column(5, "Product details", "Salt / Combination (Generic master)", "Content", "composition", "Mapped", None, _M),
    Column(6, "Product details", "Generic (product master)", "Generic Name", "genericName", "Mapped", None, _M),
    Column(7, "Product details", "-", "Strength", "strength", "Mapped", None, _M),
    Column(8, "Product details", "Category (TABLET, SYRUP...)", "Medicine Form", "drugForm", "Mapped", "drugForm", _D),
    Column(9, "Product details", "Brand (product master)", "Brand", "brandId", "Mapped", None, _M),
    Column(10, "Product details", "Manufacturer (MFR)", "Manufacturer", "", "Missing", "manufacturer", _I),
    Column(11, "Product details", "Category (product master)", "Category", "categoryId", "Mapped", None, _M),
    Column(12, "Product details", "Sub Category (product master)", "Sub Category", "subCategoryId", "Mapped", None, _M),
    Column(13, "Product details", "Division (MEDICINE / HOMEO)", "Medicine Type", "", "Missing", None, _M),
    Column(14, "Product details", "Drug Schedule", "Drug Schedule", "drugSchedule", "Mapped", None, _M),
    Column(15, "Product details", "-", "Needs Prescription", "prescriptionRequired", "Mapped", None, _M),
    Column(16, "Product details", "Uses (Generic master)", "Uses", "", "Missing", None, _M),
    Column(17, "Product details", "HSN", "HSN Code", "hsnCode", "Mapped", "hsnCode", _I),
    Column(18, "Product details", "-", "Storage", "", "Missing", None, _M),
    Column(19, "Product details", "Rack / Location", "Rack", "rackNumber / location", "Mapped", None, _S),
    # --- Pack & unit ---------------------------------------------------
    Column(20, "Pack & unit", "Q.Box (product master)", "Pack Size", "packSize", "Mapped", "packSize", _D),
    Column(21, "Pack & unit", "Unit (product master)", "Base Unit", "unitId", "Mapped", None, _M),
    Column(22, "Pack & unit", "Q.Box (on line)", "Units per Pack", "product.packSize (not on line)", "Partial", "packSize", _D),
    # --- Batch & stock -------------------------------------------------
    Column(23, "Batch & stock", "Has Batch No / Expiry / Multiple Batches", "Batch Tracking", "requiresBatch", "Partial", None, _M),
    Column(24, "Batch & stock", "Batch No", "Batch No", "batch", "Mapped", "batch", _I),
    Column(25, "Batch & stock", "-", "Mfg Date", "manufactureDate", "Mapped", None, _S),
    Column(26, "Batch & stock", "Exp. Date", "Expiry", "expiryDate", "Mapped", "expiryDate", _I),
    Column(27, "Batch & stock", "Godown", "Godown", "", "Missing", None, _S),
    # --- Quantity ------------------------------------------------------
    Column(28, "Quantity", "Pkg.Qty", "Packs", "", "Missing", "packs", _I),
    Column(29, "Quantity", "Pkg.Free", "Free Packs", "", "Missing", "freePacks", _I),
    Column(30, "Quantity", "Pkg.Replace", "Replacement Packs", "", "Missing", "replacementPacks", _I),
    Column(31, "Quantity", "Qty", "Qty", "quantity", "Mapped", "quantity", _D),
    Column(32, "Quantity", "Free", "Free Qty", "freeQuantity", "Mapped", "freeQuantity", _D),
    Column(33, "Quantity", "Replace", "Replacement Qty", "", "Missing", "replacementQuantity", _D),
    Column(34, "Quantity", "Total Qty", "Total Qty", "", "Missing", "totalQuantity", _D),
    Column(35, "Quantity", "-", "Scheme", "", "Missing", "scheme", _D),
    # --- Price ---------------------------------------------------------
    Column(36, "Price", "Pkg.MRP", "MRP / Pack", "", "Missing", "mrpPerPack", _I),
    Column(37, "Price", "MRP", "MRP / Unit", "mrp", "Mapped", "mrp", _D),
    Column(38, "Price", "Pkg.Rate", "Rate / Pack", "", "Missing", "ratePerPack", _I),
    Column(39, "Price", "Rate", "Rate / Unit", "rate / purchaseRate", "Mapped", "purchaseRate", _D),
    Column(40, "Price", "-", "Retailer Price (PTR)", "product.wholesalePrice (product-level)", "Partial", None, _M),
    Column(41, "Price", "-", "Stockist Price (PTS)", "", "Missing", None, _M),
    # --- Amount & discount ---------------------------------------------
    Column(42, "Amount & discount", "Gross Amt", "Gross Amt", "purchaseAmount", "Partial", "grossAmount", _D),
    Column(43, "Amount & discount", "Disc%", "Disc %", "discountPercent", "Mapped", "discountPercent", _I),
    Column(44, "Amount & discount", "Disc Amt", "Disc Amt", "discountAmount", "Mapped", "discountAmount", _I),
    Column(45, "Amount & discount", "Addl Disc%", "Extra Disc %", "", "Missing", None, _S),
    Column(46, "Amount & discount", "Addl Disc Amt", "Extra Disc Amt", "adjustment", "Partial", None, _S),
    Column(47, "Amount & discount", "Spcl Disc%", "Special Disc %", "", "Missing", "specialDiscountPercent", _I),
    Column(48, "Amount & discount", "Spcl Disc Amt", "Special Disc Amt", "", "Missing", "specialDiscountAmount", _D),
    Column(49, "Amount & discount", "Amount", "Amount", "purchaseAmount", "Mapped", "purchaseAmount", _I),
    # --- Tax & total ---------------------------------------------------
    Column(50, "Tax & total", "Tax", "Tax %", "taxPercent", "Mapped", "taxPercent", _I),
    Column(51, "Tax & total", "CGST Amt", "CGST", "- (only taxAmount)", "Partial", "cgstAmount", _I),
    Column(52, "Tax & total", "SGST Amt", "SGST", "- (only taxAmount)", "Partial", "sgstAmount", _I),
    Column(53, "Tax & total", "Cess Amt", "Cess Amt", "", "Missing", "cessAmount", _I),
    Column(54, "Tax & total", "Total", "Total", "billedAmount", "Mapped", "billedAmount", _I),
    # --- Cost ----------------------------------------------------------
    Column(55, "Cost", "Add. Cost +", "Extra Cost", "- (invoice freightAmount only)", "Missing", None, _S),
    Column(56, "Cost", "Add. Cost -", "Cost Deduction", "", "Missing", None, _S),
    Column(57, "Cost", "Cost", "Landing Cost / Pack", "purchaseCost / billedCost", "Mapped", "landingCostPerPack", _D),
    Column(58, "Cost", "-", "Landing Cost / Unit", "- (Cost / Pack Size)", "Missing", "landingCostPerUnit", _D),
    Column(59, "Cost", "Prv Cost / Cur Cost (shown in Product cell)", "Previous Cost", "", "Missing", None, _S),
    # --- Selling price setup -------------------------------------------
    Column(60, "Selling price setup", "Calc.Mode", "Price Based On", "", "Missing", None, _S),
    Column(61, "Selling price setup", "RETAIL Calc %", "Retail %", "", "Missing", None, _S),
    Column(62, "Selling price setup", "MRP %", "MRP %", "", "Missing", None, _S),
    Column(63, "Selling price setup", "RETAIL Rate", "Retail Rate / Pack", "salePrice", "Mapped", None, _S),
    Column(64, "Selling price setup", "-", "Retail Rate / Unit", "", "Missing", None, _S),
    Column(65, "Selling price setup", "Margin %", "Margin %", "profitAmount (amount, not %)", "Partial", None, _S),
    Column(66, "Selling price setup", "RATE 6", "Rate 6", "product.wholesalePrice (product-level)", "Partial", None, _S),
    Column(67, "Selling price setup", "RATE 7", "Rate 7", "product.retailPrice (product-level)", "Partial", None, _S),
    Column(68, "Selling price setup", "RATE 8", "Rate 8", "product.bulkPrice (product-level)", "Partial", None, _S),
    Column(69, "Selling price setup", "RATE 9", "Rate 9", "", "Missing", None, _S),
    Column(70, "Selling price setup", "RATE 10", "Rate 10", "", "Missing", None, _S),
)]

assert len(COLUMNS) == 70
assert all(c.extracted_as is None or c.extracted_as in PurchaseLine.model_fields for c in COLUMNS)


# Invoice header spellings per column, from the "OCR" column of the mapping
# sheet. Every header here must resolve through ``parser._COLUMN_SYNONYMS``.
OCR_HEADERS: Dict[int, Tuple[str, ...]] = {
    4: ("DESCRIPTION", "Products", "Item Description"),
    10: ("MFR", "Manufacturer"),
    17: ("HSN", "HSN/SAC"),
    22: ("PKG", "Pack"),
    24: ("BATCH",),
    26: ("EX.DT", "Expiry Date", "Expiry"),
    31: ("QTY", "Quantity"),
    36: ("M.R.P", "MRP"),
    38: ("RATE",),
    43: ("DIS%", "DISC %"),
    49: ("AMOUNT",),
    50: ("GST%", "TAX %"),
    54: ("Net Amt", "Net Amount", "grand total"),
}

assert all(COLUMNS[no - 1].extracted_as for no in OCR_HEADERS)


def columns_by_group() -> Dict[str, List[Column]]:
    groups: Dict[str, List[Column]] = {}
    for col in COLUMNS:
        groups.setdefault(col.group, []).append(col)
    return groups


def coverage(lines: Iterable[PurchaseLine]) -> List[dict]:
    """Per-column fill rate for extracted ``lines``.

    One entry per catalog column that the extractor targets; ``filled`` is the
    number of lines where the field is non-null.
    """
    lines = list(lines)
    out = []
    for col in COLUMNS:
        if col.extracted_as is None:
            continue
        filled = sum(1 for ln in lines if getattr(ln, col.extracted_as) is not None)
        out.append({"no": col.no, "ai_pharma": col.ai_pharma, "field": col.extracted_as,
                    "source": col.source, "filled": filled, "total": len(lines)})
    return out
