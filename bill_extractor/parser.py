"""Rule-based mapping from Docling's OCR text + tables into the Invoice schema.

This layer is intentionally tolerant: every extractor returns ``None`` (or skips
a row) rather than raising, so a single OCR glitch never breaks the whole parse.
The raw Docling output is retained on the result as a fallback.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .schema import Invoice, LineItem, Party, RawOutput, TaxLine, Totals

# --- regexes ---------------------------------------------------------------

GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]{3}\b")
DATE_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")
PIN_RE = re.compile(r"\b\d{6}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DOC_CODE_RE = re.compile(r"\b[A-Z]{1,4}-\d{2}(?:-\d{2})?-\d{3,6}\b")
STATE_CODE_RE = re.compile(r"State Code[:\s]*([0-9]{1,2})", re.IGNORECASE)


# --- value helpers ---------------------------------------------------------


def to_float(value: Any) -> Optional[float]:
    """Parse a money/number string (handles ``₹``, ``Rs``, commas) to float."""
    if value is None:
        return None
    text = str(value).replace(",", "")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in ("", "-", ".", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _lines(markdown: str) -> List[str]:
    out = []
    for raw in markdown.splitlines():
        line = raw.strip().strip("|").strip()
        if line:
            out.append(line)
    return out


_LABEL_LINE_RE = re.compile(r"^\s*[A-Za-z][\w .#/&]{0,24}\s*[:#]")


def _looks_like_label(line: str) -> bool:
    """True if a line is itself a ``Key :`` style label (so not a value)."""
    return bool(_LABEL_LINE_RE.match(line))


def _value_after_label(lines: List[str], labels: List[str]) -> Optional[str]:
    """Return the value following ``label:`` on the same line, else the next line.

    The label must sit at the start of the line and be a whole word, and the
    inline form requires a ``:``/``#`` separator — this avoids loose labels like
    ``lr`` swallowing ``LR No`` and prevents grabbing an adjacent labelled field.
    """
    label_res = [re.compile(rf"^\s*{lbl}\b\s*[:#]\s*(.+)$", re.IGNORECASE) for lbl in labels]
    bare_res = [re.compile(rf"^\s*{lbl}\b\s*[:#]?\s*$", re.IGNORECASE) for lbl in labels]
    for i, line in enumerate(lines):
        for rx in label_res:
            m = rx.search(line)
            if m:
                value = _clean_value(m.group(1))
                if value:
                    return value
        for rx in bare_res:
            if rx.match(line):
                if i + 1 < len(lines) and _is_grabbable_value(lines[i + 1]):
                    value = _clean_value(lines[i + 1])
                    if value:
                        return value
                return None
    return None


def _is_grabbable_value(line: str) -> bool:
    """Whether a line can serve as a label's value (not a label/totals row)."""
    if _looks_like_label(line):
        return False
    if re.search(r"\d+\.\d{2}", line):  # monetary amount -> a totals/table row
        return False
    if re.search(r"\b(total|gst|grand|taxable|hsn|sln)\b", line, re.IGNORECASE):
        return False
    return True


def _clean_value(text: Optional[str]) -> Optional[str]:
    """Strip surrounding punctuation; return None if nothing meaningful remains."""
    if text is None:
        return None
    value = text.strip().strip("|").strip(" :#-")
    return value if re.search(r"\w", value) else None


def _find_doc_code(text: str, exclude: set[str]) -> Optional[str]:
    for m in DOC_CODE_RE.findall(text):
        if m not in exclude:
            return m
    return None


# --- section parsers -------------------------------------------------------


def parse_seller(markdown: str, gstins: List[str]) -> Party:
    lines = _lines(markdown)
    party = Party()
    if lines:
        party.name = lines[0]
    if gstins:
        party.gstin = gstins[0]
    email = EMAIL_RE.search(markdown)
    if email:
        party.email = email.group(0)
    phone = PHONE_RE.search(markdown)
    if phone:
        party.phone = phone.group(0)
    sc = STATE_CODE_RE.search(markdown)
    if sc:
        party.state_code = sc.group(1)
    return party


def _party_from_block(block: str, gstin: Optional[str]) -> Party:
    party = Party(gstin=gstin)
    block_lines = [l.strip() for l in block.splitlines() if l.strip()]
    if block_lines:
        party.name = block_lines[0]
    party.address_lines = block_lines[1:]
    # Prefer a GSTIN/email/phone found inside the block itself.
    own_gstin = GSTIN_RE.search(block.upper())
    if own_gstin:
        party.gstin = own_gstin.group(0)
    email = EMAIL_RE.search(block)
    if email:
        party.email = email.group(0)
    phone = PHONE_RE.search(block)
    if phone:
        party.phone = phone.group(0)
    pin = PIN_RE.search(block)
    if pin:
        party.pin = pin.group(0)
    sc = STATE_CODE_RE.search(block)
    if sc:
        party.state_code = sc.group(1)
    return party


def parse_parties(markdown: str, gstins: List[str]) -> tuple[Party, Party]:
    """Best-effort split of Bill To / Ship To blocks.

    Falls back to GSTIN ordering (seller, bill_to, ship_to) when label-based
    block detection is unreliable.
    """
    bill_gstin = gstins[1] if len(gstins) > 1 else None
    ship_gstin = gstins[2] if len(gstins) > 2 else (gstins[1] if len(gstins) > 1 else None)

    bill_block = _grab_block(markdown, r"bill\s*to")
    ship_block = _grab_block(markdown, r"ship\s*to")

    bill_to = _party_from_block(bill_block, bill_gstin) if bill_block else Party(gstin=bill_gstin)
    ship_to = _party_from_block(ship_block, ship_gstin) if ship_block else Party(gstin=ship_gstin)
    return bill_to, ship_to


def _grab_block(markdown: str, label_pattern: str, max_lines: int = 8) -> Optional[str]:
    """Grab the lines following a label (e.g. 'Bill To') up to the next blank-ish stop."""
    lines = markdown.splitlines()
    label_re = re.compile(label_pattern, re.IGNORECASE)
    stop_re = re.compile(r"(transporter|agent|remarks|hsn|sln|^\s*$)", re.IGNORECASE)
    for i, line in enumerate(lines):
        if label_re.search(line):
            # Text after the label on the same line counts too.
            after = label_re.sub("", line).strip(" :|")
            collected = [after] if after else []
            for nxt in lines[i + 1 : i + 1 + max_lines]:
                if stop_re.search(nxt) and not nxt.strip():
                    break
                if re.match(r"^\s*(transporter|agent|remarks|hsn|sln)", nxt, re.IGNORECASE):
                    break
                if nxt.strip():
                    collected.append(nxt.strip())
            if collected:
                return "\n".join(collected)
    return None


def parse_meta(markdown: str, invoice: Invoice) -> None:
    lines = _lines(markdown)
    full = markdown

    invoice.invoice_no = _value_after_label(lines, ["invoice no", "invoice"]) or _find_doc_code(full, set())
    if invoice.invoice_no:
        invoice.invoice_no = invoice.invoice_no.split()[0] if invoice.invoice_no else None

    date = _value_after_label(lines, ["date"])
    dm = DATE_RE.search(date) if date else None
    if not dm:
        dm = DATE_RE.search(full)
    if dm:
        invoice.invoice_date = dm.group(0)

    invoice.transporter = _value_after_label(lines, ["transporter"])
    invoice.destination = _value_after_label(lines, ["destination"])
    invoice.lr_no = _value_after_label(lines, ["lr no", "l.r. no", "lr"])
    invoice.agent = _value_after_label(lines, ["agent"])
    invoice.doc_no = _value_after_label(lines, ["doc no"])
    invoice.packet_no = _value_after_label(lines, ["packet no"])

    challan = _value_after_label(lines, ["challan no", "challan"])
    if challan:
        cm = DOC_CODE_RE.search(challan)
        invoice.challan_no = cm.group(0) if cm else challan.split()[0]
    so = _value_after_label(lines, ["so no", "so no.", "s.o no"])
    if so:
        som = DOC_CODE_RE.search(so)
        invoice.so_no = som.group(0) if som else so.split()[0]

    words = re.search(r"amount\s*in\s*words\s*[:|]?\s*(.+)", full, re.IGNORECASE)
    if words:
        invoice.amount_in_words = words.group(1).strip().strip("|").strip()

    invoice.prepared_by = _value_after_label(lines, ["prepared by"])
    invoice.checked_by = _value_after_label(lines, ["checked by"])
    invoice.delivered_by = _value_after_label(lines, ["delivered by"])


# --- table parsers ---------------------------------------------------------


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _match_col(headers: List[str], *keywords: str) -> Optional[str]:
    for h in headers:
        n = _norm(h)
        if any(k in n for k in keywords):
            return h
    return None


def _is_tax_table(headers: List[str]) -> bool:
    joined = " ".join(_norm(h) for h in headers)
    return ("cgst" in joined or "sgst" in joined or "igst" in joined) and "hsn" in joined


def _is_line_item_table(headers: List[str]) -> bool:
    joined = " ".join(_norm(h) for h in headers)
    has_qty = "qty" in joined or "quantity" in joined
    has_value = "value" in joined or "amount" in joined
    has_hsn = "hsn" in joined
    return has_hsn and (has_qty or has_value) and not _is_tax_table(headers)


def parse_line_items(tables: List[List[Dict[str, Any]]]) -> List[LineItem]:
    items: List[LineItem] = []
    for rows in tables:
        if not rows:
            continue
        headers = list(rows[0].keys())
        if not _is_line_item_table(headers):
            continue

        col_sln = _match_col(headers, "sln", "sno", "slno", "sl")
        col_group = _match_col(headers, "group", "item", "particular")
        col_desc = _match_col(headers, "description", "desc")
        col_hsn = _match_col(headers, "hsn")
        col_mrp = _match_col(headers, "mrp")
        col_rate = _match_col(headers, "rate")
        col_qty = _match_col(headers, "qty", "quantity")
        col_uom = _match_col(headers, "uom", "unit")
        col_value = _match_col(headers, "value", "amount")

        for row in rows:
            label_text = _norm(" ".join(str(v) for v in row.values() if v))
            # Skip embedded total / tax rows.
            if any(k in label_text for k in ("total", "grandtotal", "stategst", "centralgst")):
                continue
            item = LineItem(
                sln=_get(row, col_sln),
                group_item=_get(row, col_group),
                description=_get(row, col_desc) or _get(row, col_group),
                hsn_code=_get(row, col_hsn),
                mrp=to_float(_get(row, col_mrp)),
                rate=to_float(_get(row, col_rate)),
                qty=to_float(_get(row, col_qty)),
                uom=_get(row, col_uom),
                value=to_float(_get(row, col_value)),
            )
            # Require at least an HSN or a value to count as a real line.
            if item.hsn_code or item.value is not None:
                items.append(item)
        if items:
            break
    return items


def parse_tax_summary(tables: List[List[Dict[str, Any]]]) -> List[TaxLine]:
    out: List[TaxLine] = []
    for rows in tables:
        if not rows:
            continue
        headers = list(rows[0].keys())
        if not _is_tax_table(headers):
            continue
        col_hsn = _match_col(headers, "hsn", "sac")
        col_tax = _match_col(headers, "taxable")
        col_cgst = _match_col(headers, "cgst")
        col_sgst = _match_col(headers, "sgst")
        col_igst = _match_col(headers, "igst")
        for row in rows:
            cgst_rate, cgst_amt = _split_rate_amount(_get(row, col_cgst))
            sgst_rate, sgst_amt = _split_rate_amount(_get(row, col_sgst))
            igst_rate, igst_amt = _split_rate_amount(_get(row, col_igst))
            line = TaxLine(
                hsn_sac=_get(row, col_hsn),
                taxable_amount=to_float(_get(row, col_tax)),
                cgst_rate=cgst_rate,
                cgst_amount=cgst_amt,
                sgst_rate=sgst_rate,
                sgst_amount=sgst_amt,
                igst_rate=igst_rate,
                igst_amount=igst_amt,
            )
            if line.hsn_sac or line.taxable_amount is not None:
                out.append(line)
        if out:
            break
    return out


def _split_rate_amount(cell: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """From a cell like ``(2.5%) ₹ 264.00`` return ``(rate, amount)``."""
    if cell is None:
        return None, None
    rate_m = re.search(r"(\d+(?:\.\d+)?)\s*%", cell)
    rate = float(rate_m.group(1)) if rate_m else None
    money = re.findall(r"[\d,]+\.\d{2}", cell)
    amount = to_float(money[-1]) if money else to_float(re.sub(r".*%", "", cell))
    return rate, amount


def parse_totals(markdown: str, tables: List[List[Dict[str, Any]]]) -> Totals:
    totals = Totals()
    # Build a searchable text from markdown + all table rows.
    chunks = [markdown]
    for rows in tables:
        for row in rows:
            chunks.append(" ".join(str(v) for v in row.values() if v is not None))
    text = "\n".join(chunks)

    totals.grand_total = _amount_for(text, r"grand\s*total")
    totals.taxable_amount = _amount_for(text, r"taxable\s*amount")
    totals.sgst = _amount_for(text, r"(?:state\s*gst|sgst)")
    totals.cgst = _amount_for(text, r"(?:central\s*gst|cgst)")
    totals.igst = _amount_for(text, r"(?:integrated\s*gst|igst)")

    # Total qty: a "Total" line that is not the grand total.
    for line in text.splitlines():
        if re.search(r"\btotal\b", line, re.IGNORECASE) and not re.search(
            r"grand|taxable|gst", line, re.IGNORECASE
        ):
            nums = [to_float(n) for n in re.findall(r"\d[\d,]*\.?\d*", line)]
            nums = [n for n in nums if n is not None]
            if nums:
                totals.total_qty = nums[0]
            break
    return totals


def _amount_for(text: str, label_pattern: str) -> Optional[float]:
    """Find the last money-looking number on the line containing ``label``."""
    for line in text.splitlines():
        if re.search(label_pattern, line, re.IGNORECASE):
            money = re.findall(r"[₹Rs.]*\s*[\d,]+\.\d{2}", line)
            if money:
                return to_float(money[-1])
            nums = re.findall(r"[\d,]+\.?\d*", line)
            vals = [to_float(n) for n in nums if to_float(n) is not None]
            if vals:
                return vals[-1]
    return None


def _get(row: Dict[str, Any], col: Optional[str]) -> Optional[str]:
    if col is None:
        return None
    val = row.get(col)
    if val is None:
        return None
    s = str(val).strip()
    return s or None


# --- entry point -----------------------------------------------------------


def parse(markdown: str, tables: List[List[Dict[str, Any]]]) -> Invoice:
    """Map Docling markdown + tables into a structured :class:`Invoice`."""
    gstins: List[str] = []
    for g in GSTIN_RE.findall((markdown or "").upper()):
        if g not in gstins:
            gstins.append(g)

    invoice = Invoice()
    invoice.seller = parse_seller(markdown, gstins)
    invoice.bill_to, invoice.ship_to = parse_parties(markdown, gstins)
    parse_meta(markdown, invoice)
    invoice.line_items = parse_line_items(tables)
    invoice.tax_summary = parse_tax_summary(tables)
    invoice.totals = parse_totals(markdown, tables)
    _reconcile_totals(invoice)
    invoice.raw = RawOutput(markdown=markdown, tables=tables)
    return invoice


def _reconcile_totals(invoice: Invoice) -> None:
    """Fill obvious gaps in totals from the tax summary / line items."""
    if invoice.totals.taxable_amount is None:
        tax_sum = sum(t.taxable_amount for t in invoice.tax_summary
                      if t.taxable_amount is not None)
        if tax_sum:
            invoice.totals.taxable_amount = round(tax_sum, 2)
        else:
            item_sum = sum(i.value for i in invoice.line_items if i.value is not None)
            if item_sum:
                invoice.totals.taxable_amount = round(item_sum, 2)
    if invoice.totals.total_qty is None and invoice.line_items:
        qty_sum = sum(i.qty for i in invoice.line_items if i.qty is not None)
        if qty_sum:
            invoice.totals.total_qty = qty_sum
