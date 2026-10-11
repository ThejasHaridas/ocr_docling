"""Project parsed invoice lines onto the purchase-invoice data model.

Supplier invoices print quantities and prices per *pack* (a strip, bottle or
tube). The AI Pharma model also carries per-*unit* figures, so this module
normalises each :class:`~bill_extractor.schema.LineItem` into a
:class:`~bill_extractor.schema.PurchaseLine`::

    quantity (units) = packs x packSize
    mrp (per unit)   = mrpPerPack / packSize
    purchaseRate     = ratePerPack / packSize
    landingCost      = purchaseAmount / (packs + free + replacement)

See ``column_mapping.py`` for which of the 70 mapped columns are filled.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .schema import LineItem, PurchaseLine

def round2(value: float, places: int = 2) -> float:
    return round(value + 0.0, places)


# Drug form inferred from tokens in the product name (first match wins).
_FORM_TOKENS = (
    (r"\b(tabs?|tablets?)\b", "Tablet"),
    (r"\b(caps?|capsules?)\b", "Capsule"),
    (r"\b(syp|syrup|susp|suspension)\b", "Syrup"),
    (r"\b(drops?|rops)\b", "Drops"),
    (r"\b(cream|crm)\b", "Cream"),
    (r"\b(gel)\b", "Gel"),
    (r"\b(lotion|lot)\b", "Lotion"),
    (r"\b(oint|ointment)\b", "Ointment"),
    (r"\b(inj|injection|cartridge|vial)\b", "Injection"),
    (r"\b(soap|bar)\b", "Soap"),
    (r"\b(shampoo)\b", "Shampoo"),
    (r"\b(powder)\b", "Powder"),
    (r"\b(jelly)\b", "Jelly"),
    (r"\b(inhaler|transhaler|rotacap)\b", "Inhaler"),
)

# "10's", "15s", "1X10", "14'S" -> N units per pack.
_COUNT_PACK_RE = re.compile(r"^\s*(?:1\s*[xX]\s*)?(\d+)\s*'?\s*[sS]?\s*$")
# "30Gr", "100ML", "250gm", "1X60ML", "3ml" -> a single container.
_CONTAINER_RE = re.compile(r"(?:\d+\s*[xX]\s*)?\d+(?:\.\d+)?\s*(?:ml|gm|gr|g|mg|iu|l)\b", re.IGNORECASE)
_KIT_RE = re.compile(r"kit\b", re.IGNORECASE)


def parse_pack_size(label: Optional[str]) -> Optional[int]:
    """Units per pack from a pack label, or ``None`` when it can't be told.

    Counted packs (``10's``, ``15s``, ``1X10``) give their count; liquid/semi-
    solid containers (``100ML``, ``30Gr``, ``1X60ML``) and kits count as 1.
    """
    if not label:
        return None
    text = label.strip()
    if _COUNT_PACK_RE.match(text):
        count = int(_COUNT_PACK_RE.match(text).group(1))
        return count or None
    if _CONTAINER_RE.search(text) or _KIT_RE.search(text):
        return 1
    return None


_DESC_PACK_RE = re.compile(
    r"(?:\b(\d+\s*'\s*[sS])|\b(\d+[sS])|\b(\d+(?:\.\d+)?\s*(?:ml|gm|gr|g)))\s*$", re.IGNORECASE)


def pack_label_from_description(desc: Optional[str]) -> Optional[str]:
    """Pack label printed at the end of the product name, e.g. ``... TAB 10's``."""
    m = _DESC_PACK_RE.search(desc or "")
    return next((g for g in m.groups() if g), None) if m else None


def infer_drug_form(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    for pattern, form in _FORM_TOKENS:
        if re.search(pattern, name, re.IGNORECASE):
            return form
    return None


def _expiry(raw: Optional[str]) -> Optional[str]:
    """Normalise ``07-28`` / ``07/28`` / ``8 /29`` to ``MM/YY`` (kept as printed otherwise)."""
    if not raw:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})\s*[-/]\s*(\d{2,4})\s*", raw)
    if not m:
        return raw.strip()
    month, year = int(m.group(1)), m.group(2)
    if not 1 <= month <= 12:
        return raw.strip()
    return f"{month:02d}/{year}"


def _scheme(packs: Optional[float], free: Optional[float]) -> Optional[str]:
    if not packs or not free:
        return None
    fmt = lambda v: str(int(v)) if float(v).is_integer() else str(v)  # noqa: E731
    return f"{fmt(packs)} + {fmt(free)}"


def build_purchase_line(item: LineItem, seq: int) -> PurchaseLine:
    pack_label = item.pack or pack_label_from_description(item.description)
    pack_size = parse_pack_size(pack_label)
    packs = item.qty
    free = item.free_qty
    repl = item.replace_qty
    total_packs = (packs or 0) + (free or 0) + (repl or 0) if any(
        v is not None for v in (packs, free, repl)) else None

    def units(v: Optional[float]) -> Optional[float]:
        return round2(v * pack_size) if v is not None and pack_size else None

    def per_unit(v: Optional[float]) -> Optional[float]:
        return round2(v / pack_size, 4) if v is not None and pack_size else None

    gross = round2(packs * item.rate) if packs is not None and item.rate is not None else None

    # Amount column is taxable value; when it still equals gross the discount
    # is printed separately and has not been applied yet.
    disc_amt = item.discount_amount
    if disc_amt is None and item.discount_pct and gross is not None:
        disc_amt = round2(gross * item.discount_pct / 100)
    purchase = item.value
    if purchase is None and gross is not None:
        purchase = round2(gross - (disc_amt or 0))
    elif (purchase is not None and gross is not None and disc_amt
          and abs(purchase - gross) < 0.05):
        purchase = round2(gross - disc_amt)
    if disc_amt is None and purchase is not None and gross is not None and gross - purchase > 0.05:
        disc_amt = round2(gross - purchase)  # discount implied by gross vs amount

    spcl_amt = (round2(purchase * item.sch_discount_pct / 100)
                if purchase is not None and item.sch_discount_pct else None)

    tax_amt = item.gst_amount
    if tax_amt is None and None not in (item.cgst_amount, item.sgst_amount):
        tax_amt = round2(item.cgst_amount + item.sgst_amount + (item.cess_amount or 0))
    if tax_amt is None and purchase is not None and item.gst_pct is not None:
        tax_amt = round2(purchase * item.gst_pct / 100)
    cgst, sgst = item.cgst_amount, item.sgst_amount
    if tax_amt is not None and cgst is None and sgst is None:
        cgst = round2(tax_amt / 2)  # intra-state: tax splits evenly
        sgst = round2(tax_amt - cgst)

    billed = item.net_amount
    if billed is None and purchase is not None and tax_amt is not None:
        billed = round2(purchase + tax_amt)

    landing_pack = (round2(purchase / total_packs) if purchase is not None and total_packs else None)

    return PurchaseLine(
        sequenceNumber=int(item.sln) if item.sln and item.sln.isdigit() else seq,
        itemName=item.description,
        manufacturer=item.mfr,
        hsnCode=item.hsn_code,
        drugForm=infer_drug_form(item.description),
        packLabel=pack_label,
        packSize=pack_size,
        batch=item.batch,
        expiryDate=_expiry(item.expiry),
        packs=packs,
        freePacks=free,
        replacementPacks=repl,
        quantity=units(packs),
        freeQuantity=units(free),
        replacementQuantity=units(repl),
        totalQuantity=units(total_packs),
        scheme=_scheme(packs, free),
        mrpPerPack=item.mrp,
        mrp=per_unit(item.mrp),
        ratePerPack=item.rate,
        purchaseRate=per_unit(item.rate),
        grossAmount=gross,
        discountPercent=item.discount_pct,
        discountAmount=disc_amt,
        specialDiscountPercent=item.sch_discount_pct,
        specialDiscountAmount=spcl_amt,
        purchaseAmount=purchase,
        taxPercent=item.gst_pct,
        cgstAmount=cgst,
        sgstAmount=sgst,
        cessAmount=item.cess_amount,
        taxAmount=tax_amt,
        billedAmount=billed,
        landingCostPerPack=landing_pack,
        landingCostPerUnit=per_unit(landing_pack),
    )


def build_purchase_lines(items: List[LineItem]) -> List[PurchaseLine]:
    return [build_purchase_line(it, i) for i, it in enumerate(items, start=1)]
