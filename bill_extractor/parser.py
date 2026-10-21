"""Rule-based mapping from Docling's OCR text + tables into the Invoice schema.

This layer is intentionally tolerant: every extractor returns ``None`` (or skips
a row) rather than raising, so a single OCR glitch never breaks the whole parse.
The raw Docling output is retained on the result as a fallback.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .purchase import build_purchase_lines
from .schema import Invoice, LineItem, Party, RawOutput, TaxLine, Totals

# --- regexes ---------------------------------------------------------------

_GSTIN_CORE = r"\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]{3}"
GSTIN_RE = re.compile(rf"\b{_GSTIN_CORE}\b")
GST_NO_RE = re.compile(rf"gst\s*no\.?\s*[:#]?\s*({_GSTIN_CORE})", re.IGNORECASE)
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
DATE_RE = re.compile(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")
PIN_RE = re.compile(r"\b\d{6}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@\s?[\w-]+\.[\w.-]+")
# Invoice-code shapes seen across templates: RR-25-26-00306 (hyphenated) and
# UE2526-13635 / C25-31706 (letters+digits then a run).
DOC_CODE_RE = re.compile(r"\b[A-Z]{1,4}-\d{2}(?:-\d{2})?-\d{3,6}\b")
INV_CODE_RE = re.compile(r"\b[A-Z]{1,4}\d{2,4}-\d{3,6}\b")
STATE_CODE_RE = re.compile(r"State\s*(?:Code)?[:\s]*([0-9]{1,2})\b", re.IGNORECASE)
# One pharma line-item row, anchored on the 8-digit HSN and the MM-YY expiry:
#   MFR  HSN  DESCRIPTION...  BATCH  EXP  MRP  QTY  RATE  AMOUNT  GST%  GSTAMT  TOTAL
PHARMA_ROW_RE = re.compile(
    r"^(?P<mfr>[A-Za-z][\w.&/-]*)\s+(?P<hsn>\d{8})\s+(?P<desc>.+?)\s+"
    r"(?P<batch>[A-Za-z0-9][\w./-]*)\s+(?P<exp>\d{2}[-/]\d{2,4})\s+"
    r"(?P<mrp>[\d,]+\.?\d*)\s+(?P<qty>\d+)\s+(?P<rate>[\d,]+\.?\d*)\s+"
    r"(?P<amount>[\d,]+\.?\d*)\s+(?P<gst>\d+(?:\.\d+)?)\s+"
    r"(?P<gstamt>[\d,]+\.?\d*)\s+(?P<total>[\d,]+\.?\d*)\s*$"
)
# A GST-slab summary row on a single line: "12% 1260.22 75.62 75.62 151.24"
# (blanks restricted to spaces/tabs so it can't span lines onto a totals row).
GST_SLAB_RE = re.compile(
    r"(?<!\d)(?P<pct>\d{1,2})%[ \t]+(?P<taxable>[\d,]+\.\d{2})[ \t]+(?P<sgst>[\d,]+\.\d{2})"
    r"[ \t]+(?P<cgst>[\d,]+\.\d{2})[ \t]+(?P<totgst>[\d,]+\.\d{2})"
)


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
    # Allow trailing punctuation in the label ("No." / "Dt.") before the separator.
    label_res = [re.compile(rf"^\s*{lbl}\b[.\s]*[:#]\s*(.+)$", re.IGNORECASE) for lbl in labels]
    bare_res = [re.compile(rf"^\s*{lbl}\b[.\s]*[:#]?\s*$", re.IGNORECASE) for lbl in labels]
    for i, line in enumerate(lines):
        for rx in label_res:
            m = rx.search(line)
            if m:
                # Reject an adjacent labelled field ("Invoice No. : Date :").
                if _looks_like_label(m.group(1).strip()):
                    continue
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


_SELLER_NAME_STOPWORDS = (
    "invoice", "tax invoice", "copy", "mailed copy", "original", "duplicate",
    "description", "bank", "details", "distributors", "declaration", "gstin",
    "state", "jurisdiction", "authorised signatory", "for ", "e & o",
)


def parse_seller(markdown: str, gstins: List[str]) -> Party:
    party = Party()
    party.name = _seller_name(markdown)

    # The seller's own GSTIN is often labelled "GST No" (customer's is "GSTIN").
    gst_no = GST_NO_RE.search(markdown)
    if gst_no:
        party.gstin = gst_no.group(1).upper()
    elif gstins:
        party.gstin = gstins[0]

    # PAN sits inside the GSTIN (chars 3-12); prefer that over a stray PAN token.
    if party.gstin:
        party.pan = party.gstin[2:12]
    email = EMAIL_RE.search(markdown)
    if email:
        party.email = email.group(0).replace(" ", "")
    phone = PHONE_RE.search(markdown)
    if phone:
        party.phone = phone.group(0)
    sc = STATE_CODE_RE.search(markdown)
    if sc:
        party.state_code = sc.group(1)
    return party


def _seller_name(markdown: str) -> Optional[str]:
    """Best-effort seller name.

    On many templates the biller's name is the most-repeated company-style line
    (header + footer + bank block); fall back to the first non-label line.
    """
    counts: Dict[str, int] = {}
    for raw in markdown.splitlines():
        line = raw.strip()
        if not re.fullmatch(r"[A-Z][A-Z .&'-]{3,40}", line):
            continue
        low = line.lower()
        if any(sw in low for sw in _SELLER_NAME_STOPWORDS):
            continue
        counts[line] = counts.get(line, 0) + 1
    repeated = [(n, c) for n, c in counts.items() if c >= 2]
    if repeated:
        repeated.sort(key=lambda x: (-x[1], -len(x[0])))
        return repeated[0][0]
    for raw in _lines(markdown):
        if not _looks_like_label(raw) and re.search(r"[A-Za-z]", raw):
            return raw
    return None


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


def parse_parties(markdown: str, gstins: List[str],
                  seller_gstin: Optional[str] = None) -> tuple[Party, Party]:
    """Best-effort split of Bill To / Ship To blocks.

    Falls back to GSTIN ordering when label-based block detection is
    unreliable, using the GSTIN(s) that aren't the seller's for the customer.
    """
    customer = [g for g in gstins if g != seller_gstin]
    bill_gstin = customer[0] if customer else None
    ship_gstin = customer[1] if len(customer) > 1 else bill_gstin

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

    inv_no = _value_after_label(lines, ["invoice no", "inv no", "bill no", "invoice"])
    if inv_no:
        m = DOC_CODE_RE.search(inv_no) or INV_CODE_RE.search(inv_no)
        inv_no = m.group(0) if m else inv_no.split()[0]
    if not inv_no:  # order-independent fallback: first invoice-shaped code
        m = DOC_CODE_RE.search(full) or INV_CODE_RE.search(full)
        inv_no = m.group(0) if m else None
    invoice.invoice_no = inv_no

    date = _value_after_label(lines, ["invoice date", "inv date", "inv dt", "date", "dt"])
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

    invoice.amount_in_words = _parse_amount_in_words(full)

    invoice.prepared_by = _value_after_label(lines, ["prepared by", "prep by"])
    invoice.checked_by = _value_after_label(lines, ["checked by", "chkd", "ckd"])
    invoice.delivered_by = _value_after_label(lines, ["delivered by", "despatch"])


def _parse_amount_in_words(full: str) -> Optional[str]:
    """Extract the amount-in-words phrase across label variants / free text."""
    label = re.search(r"am(?:oun)?t\.?\s*in\s*words\s*[:|]?\s*(.+)", full, re.IGNORECASE)
    if label:
        value = label.group(1).splitlines()[0].strip(" |:")
        if value:
            return value
    # Free-standing phrase like "RUPEES ... ONLY" / "... Rupees Only".
    phrase = re.search(r"\b(rupees\b[\w ,.\-]*?\bonly)\b", full, re.IGNORECASE)
    if not phrase:
        phrase = re.search(r"\b([A-Z][\w ,.\-]*?\brupees?\s+only)\b", full, re.IGNORECASE)
    return phrase.group(1).strip() if phrase else None


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
    has_value = "value" in joined or "amount" in joined or "amt" in joined
    has_hsn = "hsn" in joined
    has_batch_mrp = "batch" in joined and "mrp" in joined
    return (has_hsn or has_batch_mrp) and (has_qty or has_value) and not _is_tax_table(headers)


# Header synonyms (after ``_norm``) per logical column, seen across the supplier
# templates (Chem Pharma, Fatima group, Kurikkal, Bluefox, Alankar, KIMS...).
# ``exact`` names win over ``contains`` fragments, so "Qty" beats "Sch Qty".
_COLUMN_SYNONYMS: Dict[str, Dict[str, tuple]] = {
    "sln": {"exact": ("sln", "slno", "sno", "si", "sl")},
    "group": {"exact": ("groupitem", "item")},
    "desc": {"exact": ("description", "particulars", "product", "productname", "products",
                       "itemdescription", "descriptionofgoods", "itemname"),
             "contains": ("descr", "particular")},
    "hsn": {"exact": ("hsn", "hsncode", "hsnsac"), "contains": ("hsn",)},
    "mfr": {"exact": ("mfr", "mfac", "manf", "manuf", "mfg", "manufacturer", "mkt")},
    "pack": {"exact": ("pkg", "pack", "packing", "packsize")},
    "batch": {"exact": ("batch", "batchno", "batchcode"), "contains": ("batch",)},
    "expiry": {"exact": ("exdt", "expd", "exp", "expiry", "expdate", "expirydate"),
               "contains": ("expiry", "exdt")},
    "mrp": {"exact": ("mrp",), "contains": ("mrp",)},
    "qty": {"exact": ("qty", "quantity", "billed", "eaqty"), "contains": ()},
    "free": {"exact": ("free", "fr", "freeqty", "freerepl")},
    "replace": {"exact": ("replace", "repl", "replacement", "replqty")},
    "rate": {"exact": ("rate", "srate", "tradeprice", "ptr")},
    "disc_pct": {"exact": ("dis", "disc", "discpct", "discount", "dispct")},
    "disc_amt": {"exact": ("discamt", "disamt", "discamount")},
    "sch_disc": {"exact": ("schdisc", "schdis", "scheme")},
    "gst_pct": {"exact": ("gst", "gstpct", "tax", "taxpct", "gstrate")},
    "gst_amt": {"exact": ("gstamt", "gstamount", "taxamt", "taxamount")},
    "cgst_amt": {"exact": ("cgstamt", "cgst")},
    "sgst_amt": {"exact": ("sgstamt", "sgst")},
    "cess_amt": {"exact": ("cessamt", "cess")},
    "amount": {"exact": ("amount", "amt", "grossamt", "grossamount", "gross", "taxablevalue",
                         "taxable", "taxableamt", "value", "netvalue")},
    "net": {"exact": ("netamt", "netamount", "total", "totalamt", "lineamount", "grandtotal")},
}


def _resolve_columns(headers: List[str]) -> Dict[str, Optional[str]]:
    """Map each logical column to the (first) matching header, or ``None``."""
    normed = [(h, _norm(h)) for h in headers]
    used: set[str] = set()
    cols: Dict[str, Optional[str]] = {}
    # "net" is resolved before "amount" so a lone "Total"/"Net Amt." column is
    # not swallowed as the taxable amount.
    order = [k for k in _COLUMN_SYNONYMS if k not in ("amount",)] + ["amount"]
    for key in order:
        spec = _COLUMN_SYNONYMS[key]
        found = next((h for h, n in normed if h not in used and n in spec["exact"]), None)
        if found is None:
            found = next((h for h, n in normed
                          if h not in used and any(f in n for f in spec.get("contains", ()))), None)
        if found is not None:
            used.add(found)
        cols[key] = found
    return cols


def _is_free_marker(text: Optional[str]) -> bool:
    return bool(text) and text.strip().lower() in ("free", "f", "foc")


def parse_line_items(tables: List[List[Dict[str, Any]]]) -> List[LineItem]:
    items: List[LineItem] = []
    for rows in tables:
        if not rows:
            continue
        headers = list(rows[0].keys())
        if not _is_line_item_table(headers):
            continue
        cols = _resolve_columns(headers)

        for row in rows:
            label_text = _norm(" ".join(str(v) for v in row.values() if v))
            # Skip embedded total / tax rows.
            if any(k in label_text for k in ("total", "grandtotal", "stategst", "centralgst")):
                continue
            g = lambda key: _get(row, cols[key])  # noqa: E731
            description = g("desc") or g("group")
            qty = to_float(g("qty"))
            item = LineItem(
                sln=g("sln"),
                group_item=g("group"),
                description=description,
                hsn_code=g("hsn"),
                mfr=g("mfr"),
                pack=g("pack"),
                batch=g("batch"),
                expiry=g("expiry"),
                mrp=to_float(g("mrp")),
                rate=to_float(g("rate")),
                qty=qty,
                free_qty=to_float(g("free")),
                replace_qty=to_float(g("replace")),
                uom=None,
                discount_pct=to_float(g("disc_pct")),
                discount_amount=to_float(g("disc_amt")),
                sch_discount_pct=to_float(g("sch_disc")),
                gst_pct=to_float(g("gst_pct")),
                gst_amount=to_float(g("gst_amt")),
                cgst_amount=to_float(g("cgst_amt")),
                sgst_amount=to_float(g("sgst_amt")),
                cess_amount=to_float(g("cess_amt")),
                value=to_float(g("amount")),
                net_amount=to_float(g("net")),
            )
            # Suppliers print scheme stock as a second row with rate "Free".
            if _is_free_marker(g("rate")) and qty is not None:
                prev = items[-1] if items else None
                same = (prev is not None and prev.batch == item.batch
                        and prev.description == item.description)
                if same:
                    prev.free_qty = (prev.free_qty or 0) + qty
                    continue
                item.free_qty, item.qty, item.rate = qty, 0.0, None
            # Require at least an HSN or a value to count as a real line.
            if item.hsn_code or item.value is not None:
                items.append(item)
        if items:
            break
    return items


def parse_line_items_text(markdown: str) -> List[LineItem]:
    """Parse pharma-style line items directly from text (no table available).

    Each row is a single line anchored on an 8-digit HSN and an MM-YY expiry,
    e.g. ``SUN 30049099 VORTIDIF 10 TAB 10's GTG0976A 02-27 190.00 5 135.71
    644.62 12 77.36 721.98``. ``value`` holds the taxable line amount; GST is
    kept separately in ``gst_pct`` / ``gst_amount``.
    """
    items: List[LineItem] = []
    for line in markdown.splitlines():
        m = PHARMA_ROW_RE.match(line.strip())
        if not m:
            continue
        items.append(
            LineItem(
                sln=str(len(items) + 1),
                mfr=m.group("mfr"),
                description=m.group("desc").strip(),
                hsn_code=m.group("hsn"),
                batch=m.group("batch"),
                expiry=m.group("exp"),
                mrp=to_float(m.group("mrp")),
                qty=to_float(m.group("qty")),
                rate=to_float(m.group("rate")),
                value=to_float(m.group("amount")),
                gst_pct=to_float(m.group("gst")),
                gst_amount=to_float(m.group("gstamt")),
            )
        )
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


def parse_tax_summary_text(markdown: str) -> List[TaxLine]:
    """Parse GST-slab tax rows from text, e.g. ``12% 1260.22 75.62 75.62 151.24``."""
    out: List[TaxLine] = []
    for m in GST_SLAB_RE.finditer(markdown):
        pct = to_float(m.group("pct"))
        half = round(pct / 2, 2) if pct is not None else None
        out.append(
            TaxLine(
                hsn_sac=f"{m.group('pct')}%",
                taxable_amount=to_float(m.group("taxable")),
                sgst_rate=half,
                sgst_amount=to_float(m.group("sgst")),
                cgst_rate=half,
                cgst_amount=to_float(m.group("cgst")),
            )
        )
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

    totals.grand_total = _amount_for(text, r"(?:grand\s*total|net\s*amount|net\s*payable)")
    totals.taxable_amount = _amount_for(text, r"(?:taxable\s*amount|sales\s*value|taxable\s*amt)")
    totals.discount = _amount_for(text, r"^\s*(?:discount|bill\s*disc)")
    totals.round_off = _amount_for(text, r"(?:round\s*off|r/?\.?\s*of)")
    totals.cess = _amount_for(text, r"cess")
    totals.sgst = _amount_for(text, r"(?:state\s*gst|sgst)")
    totals.cgst = _amount_for(text, r"(?:central\s*gst|cgst)")
    totals.igst = _amount_for(text, r"(?:integrated\s*gst|igst)")
    totals.tax_amount = _amount_for(text, r"tax\s*amount")

    # "TOTAL ITEM. 15 for 3" -> total_qty 15, total_items 3.
    m = re.search(r"total\s*items?\.?\s*(\d+)\s*for\s*(\d+)", text, re.IGNORECASE)
    if m:
        totals.total_qty = to_float(m.group(1))
        totals.total_items = to_float(m.group(2))
    else:
        # A plain "Total <qty> <amount>" row (first template).
        for line in text.splitlines():
            if re.search(r"\btotal\b", line, re.IGNORECASE) and not re.search(
                r"grand|taxable|gst|net|item", line, re.IGNORECASE
            ):
                nums = [to_float(n) for n in re.findall(r"\d[\d,]*\.?\d*", line)]
                nums = [n for n in nums if n is not None]
                if nums:
                    totals.total_qty = nums[0]
                break
    return totals


def _amount_for(text: str, label_pattern: str) -> Optional[float]:
    """Find the last money amount (with 2 decimals) on the line with ``label``.

    Requiring the ``.dd`` decimals avoids grabbing phone numbers, PINs or codes
    that share the line with a label in positionally-jumbled OCR text.
    """
    for line in text.splitlines():
        if re.search(label_pattern, line, re.IGNORECASE):
            money = re.findall(r"-?\s*[\d,]+\.\d{2}\b", line)
            if money:
                return to_float(money[-1])
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
    invoice.bill_to, invoice.ship_to = parse_parties(markdown, gstins, invoice.seller.gstin)
    parse_meta(markdown, invoice)
    # Prefer Docling's recognised tables; fall back to text-based parsing
    # (e.g. the offline text backend, or templates without a clean table).
    invoice.line_items = parse_line_items(tables) or parse_line_items_text(markdown)
    invoice.tax_summary = parse_tax_summary(tables) or parse_tax_summary_text(markdown)
    invoice.totals = parse_totals(markdown, tables)
    _reconcile_totals(invoice)
    invoice.purchase_lines = build_purchase_lines(invoice.line_items)
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
