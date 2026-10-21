"""Rule-based mapping from Docling's OCR text + tables into the Invoice schema.

This layer is intentionally tolerant: every extractor returns ``None`` (or skips
a row) rather than raising, so a single OCR glitch never breaks the whole parse.
The raw Docling output is retained on the result as a fallback.
"""

from __future__ import annotations

import os
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
PHONE_RE = re.compile(r"\b[6-9]\d{4}\s?\d{5}\b")
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
        line = re.sub(r"^#+\s*", "", raw.strip().strip("|").strip())
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
    # Words in a label may be joined by spaces or dots ("Inv.No", "D.L. No").
    labels = [r"[.\s]*".join(map(re.escape, lbl.split())) for lbl in labels]
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


def _cells(markdown: str) -> List[str]:
    """Every markdown table cell (and plain line) as its own line, in reading order."""
    out = []
    for raw in markdown.splitlines():
        if re.fullmatch(r"\s*\|?[\s:|-]*\|?\s*", raw):  # separator rows
            continue
        out.extend(c for c in _lines("\n".join(raw.split("|"))))
    return out


def _value_after_label_any(markdown: str, labels: List[str]) -> Optional[str]:
    return (_value_after_label(_lines(markdown), labels)
            or _value_after_label(_cells(markdown), labels))


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
    "state", "jurisdiction", "authorised signatory", "for ", "e & o", "credit bill",
    "cash bill", "estimate",
)
_NAME_RE = r"[A-Z][A-Z0-9 .&'()-]{3,50}"
# "For KOTTAKUTH DRUGS & SURGICALS" above the signature names the seller.
_SIGNATURE_RE = re.compile(rf"^\s*for\s*({_NAME_RE})\s*$", re.IGNORECASE)
# Label that opens the customer block: "To", "To.", "Bill To", "Buyer", "M/s".
_BUYER_LABEL_RE = re.compile(
    r"^\s*(?:(?:bill(?:ed)?\s*)?to|buyer|customer|m/s|consignee)\b[.:]?(?:\s|$)", re.IGNORECASE)


def split_gstins(markdown: str, gstins: List[str]) -> tuple[Optional[str], List[str]]:
    """Return ``(seller_gstin, customer_gstins)``.

    Priority: the buyer's own GSTIN(s) from ``BILL_EXTRACTOR_BUYER_GSTIN`` (a
    purchase system knows who it is); else GSTINs printed in the "To" block
    are the customer's; else the GSTIN closest below the seller's name; else a
    "GST No" label / the first GSTIN on the page.
    """
    if not gstins:
        return None, []
    known = {g.strip().upper() for g in os.getenv("BILL_EXTRACTOR_BUYER_GSTIN", "").split(",") if g.strip()}
    if known:
        sellers = [g for g in gstins if g not in known]
        if sellers:
            return sellers[0], [g for g in gstins if g != sellers[0]]

    lines = _lines(markdown)
    buyer: List[str] = []
    for i, line in enumerate(lines):
        if _BUYER_LABEL_RE.match(line):
            for nxt in lines[i:i + 8]:
                buyer += [g for g in GSTIN_RE.findall(nxt.upper()) if g not in buyer]
    others = [g for g in gstins if g not in buyer]
    if buyer and others:
        return others[0], [g for g in gstins if g != others[0]]
    if buyer and not others:  # seller's GSTIN unreadable; don't hand it the buyer's
        return None, list(gstins)

    name = _seller_name(markdown)
    if name:
        key = _norm(name)
        name_idx = next((i for i, l in enumerate(lines) if key in _norm(l)), None)
        if name_idx is not None:
            for line in lines[name_idx:name_idx + 12]:
                found = GSTIN_RE.findall(line.upper())
                if found:
                    return found[0], [g for g in gstins if g != found[0]]

    gst_no = GST_NO_RE.search(markdown)
    seller = gst_no.group(1).upper() if gst_no else gstins[0]
    return seller, [g for g in gstins if g != seller]


def parse_seller(markdown: str, gstins: List[str]) -> Party:
    party = Party()
    party.name = _seller_name(markdown)
    party.gstin, _ = split_gstins(markdown, gstins)

    # PAN sits inside the GSTIN (chars 3-12); prefer that over a stray PAN token.
    if party.gstin:
        party.pan = party.gstin[2:12]
    header = _seller_header(markdown, party.gstin)
    email = EMAIL_RE.search(header)
    if email:
        party.email = email.group(0).replace(" ", "")
    phone = PHONE_RE.search(header)
    if phone:
        party.phone = phone.group(0).replace(" ", "")
    sc = STATE_CODE_RE.search(header)
    if sc:
        party.state_code = sc.group(1)
    lines = _lines(header)
    party.dl_no = _value_after_label(lines, ["dl no", "d.l. no", "dl"])
    party.fssai = _value_after_label(lines, ["fssai", "fssai no"])
    return party


def _seller_header(markdown: str, seller_gstin: Optional[str]) -> str:
    """Text before the customer ("To") block, where the seller prints its details.

    Falls back to the whole page when no customer label is found.
    """
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if _BUYER_LABEL_RE.match(re.sub(r"^#+\s*", "", line.strip().strip("|").strip())) and i > 2:
            return "\n".join(lines[:i])
    return markdown


def _seller_name(markdown: str) -> Optional[str]:
    """Best-effort seller name.

    The "For <NAME>" signature line wins; else the most-repeated company-style
    line (header + footer + bank block); else the first non-label line.
    """
    lines = _lines(markdown)
    for line in lines:
        m = _SIGNATURE_RE.match(line)
        if m and not re.search(r"order|whats\s*app|ph\b", m.group(1), re.IGNORECASE):
            return m.group(1).strip()
    counts: Dict[str, int] = {}
    for line in lines:
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
    for raw in lines:
        low = raw.lower()
        if (not _looks_like_label(raw) and re.search(r"[A-Za-z]", raw)
                and not any(sw in low for sw in _SELLER_NAME_STOPWORDS)
                and not re.fullmatch(r"page\s*\d+\s*of\s*\d+", low)):
            return raw
    return None


def _party_from_block(block: str, gstin: Optional[str]) -> Party:
    party = Party(gstin=gstin)
    block_lines = [l.strip() for l in block.splitlines() if l.strip()]
    if block_lines:
        party.name = block_lines[0].rstrip(" -.,") or None
    party.address_lines = [l.rstrip(" -") for l in block_lines[1:] if not _PARTY_FIELD_RE.match(l)]
    dl = _value_after_label(block_lines, ["dl no", "d.l. no", "dl"])
    if dl:
        party.dl_no = dl
    # Prefer a GSTIN/email/phone found inside the block itself.
    own_gstin = GSTIN_RE.search(block.upper())
    if own_gstin:
        party.gstin = own_gstin.group(0)
    email = EMAIL_RE.search(block)
    if email:
        party.email = email.group(0)
    phone = PHONE_RE.search(block)
    if phone:
        party.phone = phone.group(0).replace(" ", "")
    # PIN from the address itself, not a 6-digit landline or licence number.
    pin = PIN_RE.search("\n".join(party.address_lines))
    if pin:
        party.pin = pin.group(0)
    sc = STATE_CODE_RE.search(block)
    if sc:
        party.state_code = sc.group(1)
    return party


_PARTY_FIELD_RE = re.compile(
    r"^(ph|phone|mob|mobile|tel|dl|d\.\s*l|gst|gstin|e-?mail|fssai|msme|pan)\b", re.IGNORECASE)
# Lines that end the customer block: the invoice-meta column or the item table.
_TO_BLOCK_STOP_RE = re.compile(
    r"\b(inv(oice)?\.?\s*(no|dt|date)|bill\s*(no|date)|date|credit\s*bill|cash\s*bill|description|"
    r"terms|order|hsn|pay\s*type|route)\b", re.IGNORECASE)


def _to_block(markdown: str, max_lines: int = 7) -> Optional[str]:
    """Customer block opened by a bare "To" / "To." label (Nilambur-style bills)."""
    lines = _lines(markdown)
    for i, line in enumerate(lines):
        m = re.match(r"^\s*to\b[.:]?\s*(.*)$", line, re.IGNORECASE)
        if not m:
            continue
        rest = [c.strip() for c in m.group(1).split("|") if c.strip()]
        collected = rest[:1]
        for nxt in lines[i + 1:i + 1 + max_lines]:
            first = nxt.split("|")[0].strip()
            if _TO_BLOCK_STOP_RE.search(first) and not _PARTY_FIELD_RE.match(first):
                break
            if first:
                collected.append(first)
        if collected and re.search(r"[A-Za-z]{3}", collected[0]):
            return "\n".join(collected)
    return None


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

    if not bill_block:
        bill_block = _to_block(markdown)
    if not bill_block and bill_gstin:
        bill_block = _block_before_gstin(markdown, bill_gstin)
    bill_to = _party_from_block(bill_block, bill_gstin) if bill_block else Party(gstin=bill_gstin)
    ship_to = _party_from_block(ship_block, ship_gstin) if ship_block else Party(gstin=ship_gstin)
    return bill_to, ship_to


_SELLER_FIELD_RE = re.compile(
    r"e-?mail|gstin|\bpan\b|fssai|msme|\bdl\s*no|tax\s*invoice|page\s*\d", re.IGNORECASE)


def _block_before_gstin(markdown: str, gstin: str, max_lines: int = 5) -> Optional[str]:
    """Customer block on unlabelled layouts (BlueFox): name + address above its GSTIN.

    Walks up from the GSTIN line until a line belonging to the seller header
    (email, GSTIN, PAN, FSSAI, DL no...). A leading account code ("13172 - ")
    is dropped from the name.
    """
    lines = _lines(markdown)
    idx = next((i for i, l in enumerate(lines) if gstin in l.upper()), None)
    if idx is None:
        return None
    block: List[str] = []
    for line in reversed(lines[max(0, idx - max_lines):idx]):
        if _SELLER_FIELD_RE.search(line):
            break
        block.insert(0, line)
    if not block:
        return None
    block[0] = re.sub(r"^\d+\s*-\s*", "", block[0])
    # Only trust it when the top line reads like a company name, not a table
    # header or a labelled field from jumbled text.
    if (not re.fullmatch(r"[A-Z][A-Z .&'()-]{3,40}", block[0])
            or re.search(r"\b(amt|gst|qty|rate|mrp|exp|total|hsn|batch)\b", block[0], re.IGNORECASE)):
        return None
    return "\n".join(block)


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

    inv_no = _value_after_label_any(markdown, ["invoice no", "inv no", "bill no", "invoice"])
    if inv_no:
        m = DOC_CODE_RE.search(inv_no) or INV_CODE_RE.search(inv_no)
        inv_no = m.group(0) if m else inv_no.replace("|", " ").split()[0]
        if not re.search(r"\d", inv_no):
            inv_no = None
    if not inv_no:  # order-independent fallback: first invoice-shaped code
        m = DOC_CODE_RE.search(full) or INV_CODE_RE.search(full)
        inv_no = m.group(0) if m else None
    invoice.invoice_no = inv_no

    date = _value_after_label_any(markdown, ["invoice date", "inv date", "inv dt", "bill date", "date", "dt"])
    dm = DATE_RE.search(date) if date else None
    if not dm:
        dm = re.search(r"(?<![\w/-])\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", full)
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
    return ("cgst" in joined or "sgst" in joined or "igst" in joined) and (
        "hsn" in joined or "taxable" in joined or "sales" in joined)


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


# --- order-free row parser --------------------------------------------------
# Docling's markdown keeps every table row's cells in order even when its
# header/column mapping goes wrong (merged header cells, skewed photos), and
# OCR text keeps the row on one line. Instead of trusting column positions,
# this parser classifies the tokens of a row by shape and pins qty / rate /
# amount by arithmetic (qty x rate = amount).

_HSN_TOKEN_RE = re.compile(r"^(?:\d{8}|\d{6}|\d{4})$")
_MONEY_TOKEN_RE = re.compile(r"^[\d,]*\d\.\d{1,3}$")
_INT_TOKEN_RE = re.compile(r"^\d{1,4}$")
_EXPIRY_TOKEN_RE = re.compile(r"^(\d{1,2})[-/](\d{2}|\d{4})$")
_PACK_TOKEN_RE = re.compile(
    r"^(?:\d+\s*'?\s*[sS]|\d*[xX]\s*\d+(?:\.\d+)?[A-Za-z]{0,3}|\d+(?:\.\d+)?(?:ml|gm|gr|g|kg|ltr|l))$",
    re.IGNORECASE)
_GST_SLABS = (0.0, 3.0, 5.0, 12.0, 18.0, 28.0)


def _row_tokens(text: str) -> List[str]:
    text = re.sub(r"(?<!\d)(\d{1,2})\s*([-/])\s*(\d{2}(?:\d{2})?)(?![\d/-])", r"\1\2\3", text)  # "8 /29"
    text = re.sub(r"(\d)\s+'\s*([sS])\b", r"\1'\2", text)  # "10 'S"
    tokens = [t.strip(",;*#") for t in re.split(r"[\s|]+", text) if t.strip(",;*#")]
    if not any(_EXPIRY_TOKEN_RE.match(t) for t in tokens):
        # Batch and expiry read as one word: "DMB-10-259/30" -> "DMB-10-25", "9/30".
        for i, t in enumerate(tokens):
            m = re.fullmatch(r"(.*[A-Za-z].*?)(\d{1,2}/\d{2})", t)
            if m and 1 <= int(m.group(2).split("/")[0]) <= 12:
                tokens[i:i + 1] = [m.group(1), m.group(2)]
                break
    return tokens


def _qty_rate_amount(ints, money, discount=None):
    """Pick (qty, rate, amount) with qty x rate ~= amount (less any discount %)."""
    factor = 1 - (discount or 0) / 100
    best = None
    for ai, amount in sorted(money, key=lambda x: -x[0]):  # amount is usually last
        for ri, rate in money:
            if ri == ai or rate <= 0:
                continue
            for qi, qty in ints:
                if qty and abs(qty * rate * factor - amount) <= max(0.05, 0.01 * amount):
                    return (qi, qty), (ri, rate), (ai, amount)
            # Qty unreadable (ticked over): amount / rate is a whole number.
            q = amount / (rate * factor) if factor else 0
            if best is None and round(q) >= 1 and abs(q - round(q)) < 0.01 and amount > rate * 0.99:
                best = ((None, float(round(q))), (ri, rate), (ai, amount))
    return best


def _item_from_row_text(text: str) -> Optional[LineItem]:
    tokens = _row_tokens(text)
    used: set = set()
    hsn_i = next((i for i, t in enumerate(tokens) if _HSN_TOKEN_RE.match(t)), None)
    if hsn_i is None:
        return None
    used.add(hsn_i)
    exp_i = next((i for i, t in enumerate(tokens) if i not in used and _EXPIRY_TOKEN_RE.match(t)
                  and 1 <= int(_EXPIRY_TOKEN_RE.match(t).group(1)) <= 12), None)
    if exp_i is not None:
        used.add(exp_i)
    pack_i = next((i for i in reversed(range(len(tokens)))
                   if i not in used and _PACK_TOKEN_RE.match(tokens[i])), None)
    if pack_i is not None:
        used.add(pack_i)
    batch_i = None
    if exp_i is not None and exp_i - 1 >= 0 and exp_i - 1 not in used and not _MONEY_TOKEN_RE.match(tokens[exp_i - 1]):
        batch_i = exp_i - 1
    if batch_i is not None:
        used.add(batch_i)

    gst_i = next((i for i, t in enumerate(tokens) if i not in used
                  and re.fullmatch(r"\d{1,2}(?:\.0{1,2})?%?", t) and "." in t
                  and float(t.rstrip("%")) in _GST_SLABS[1:]), None)  # "5.0" style, not a 0.00 discount
    if gst_i is not None:
        used.add(gst_i)
    money = [(i, to_float(t)) for i, t in enumerate(tokens) if i not in used and _MONEY_TOKEN_RE.match(t)]
    ints = [(i, float(t)) for i, t in enumerate(tokens) if i not in used and _INT_TOKEN_RE.match(t)]
    picked = _qty_rate_amount(ints, money)
    if picked is None:
        return None
    (qi, qty), (ri, rate), (ai, amount) = picked
    used.update(i for i in (qi, ri, ai) if i is not None)
    if gst_i is None:  # a bare slab number ("5", "18") after the qty/rate
        gst_i = next((i for i, v in ints if i not in used and v in _GST_SLABS and v), None)
        if gst_i is not None:
            used.add(gst_i)
    rest_money = [(i, v) for i, v in money if i not in used]
    mrp_i = next((i for i, v in sorted(rest_money, key=lambda x: -x[1]) if v >= rate), None)
    if mrp_i is not None:
        used.add(mrp_i)

    # Words: the longest run is the product name; a short run of its own is the MFR.
    runs: List[List[int]] = []
    for i, t in enumerate(tokens):
        wordish = re.search(r"[A-Za-z]", t) or (re.fullmatch(r"\d{1,4}(?:\.\d)?", t) and runs and runs[-1][-1] == i - 1)
        if i in used or not wordish:
            continue
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    runs = [[i for i in r] for r in runs]
    for r in runs:  # trailing numbers belong to the next column, not the name
        while r and not re.search(r"[A-Za-z]", tokens[r[-1]]):
            r.pop()
    runs = [r for r in runs if r]
    if not runs:
        return None
    desc_run = max(runs, key=lambda r: sum(len(re.findall(r"[A-Za-z]", tokens[i])) for i in r))
    mfr_run = next((r for r in runs if r is not desc_run and len(r) == 1
                    and re.fullmatch(r"[A-Za-z][A-Za-z.&-]{1,9}", tokens[r[0]])), None)
    if batch_i is None:
        batch_i = next((i for r in runs if r is not desc_run and r is not mfr_run for i in r
                        if re.search(r"\d", tokens[i])), None)
    gst = tokens[gst_i].rstrip("%") if gst_i is not None else None
    return LineItem(
        description=" ".join(tokens[i] for i in desc_run),
        hsn_code=tokens[hsn_i],
        mfr=tokens[mfr_run[0]] if mfr_run else None,
        pack=tokens[pack_i] if pack_i is not None else None,
        batch=tokens[batch_i] if batch_i is not None else None,
        expiry=tokens[exp_i] if exp_i is not None else None,
        mrp=to_float(tokens[mrp_i]) if mrp_i is not None else None,
        rate=rate,
        qty=qty,
        gst_pct=to_float(gst),
        value=amount,
    )


def parse_line_items_rows(markdown: str, tables: List[List[Dict[str, Any]]] = ()) -> List[LineItem]:
    """Line items from row text (markdown lines and raw table rows), order-free."""
    texts = list(markdown.splitlines())
    for rows in tables or ():
        for row in rows:
            texts.append(" ".join(str(v) for v in row.values() if v not in (None, "")))
    items: List[LineItem] = []
    seen: set = set()
    for text in texts:
        if re.search(r"\b(total|grand|taxable|sub\s*total)\b", text, re.IGNORECASE):
            continue
        item = _item_from_row_text(text)
        if item is None:
            continue
        key = (item.hsn_code, item.batch, item.qty, item.value)
        if key in seen:  # same row in markdown and in the table dump
            continue
        seen.add(key)
        item.sln = str(len(items) + 1)
        items.append(item)
    return items


def _item_is_consistent(item: LineItem) -> bool:
    """Plausible row: numeric HSN and qty x rate matching the amount."""
    if not (item.hsn_code and _HSN_TOKEN_RE.match(item.hsn_code.strip())):
        return False
    if None in (item.qty, item.rate, item.value) or not item.qty:
        return False
    gross = item.qty * item.rate
    candidates = [gross, gross * (1 - (item.discount_pct or 0) / 100)]
    if item.discount_amount:
        candidates.append(gross - item.discount_amount)
    if item.gst_pct:  # some templates print the amount incl. GST
        candidates += [c * (1 + item.gst_pct / 100) for c in list(candidates)]
    return any(abs(c - item.value) <= max(0.05, 0.01 * item.value) for c in candidates)


def _consistent_count(items: List[LineItem]) -> int:
    return sum(_item_is_consistent(i) for i in items)


def best_line_items(markdown: str, tables: List[List[Dict[str, Any]]]) -> List[LineItem]:
    """Table items when they add up; else whichever parse has more consistent rows."""
    table_items = parse_line_items(tables)
    if table_items and _consistent_count(table_items) * 2 > len(table_items):
        return table_items
    candidates = [table_items, parse_line_items_text(markdown), parse_line_items_rows(markdown, tables)]
    return max(candidates, key=lambda c: (_consistent_count(c), len(c)))


def parse_tax_summary(tables: List[List[Dict[str, Any]]]) -> List[TaxLine]:
    out: List[TaxLine] = []
    for rows in tables:
        if not rows:
            continue
        headers = list(rows[0].keys())
        if not _is_tax_table(headers):
            continue
        col_hsn = _match_col(headers, "hsn", "sac")
        col_slab = None if col_hsn else _match_col(headers, "tax", "gst")
        col_tax = _match_col(headers, "taxable") or _match_col(headers, "sales")
        col_cgst = _match_col(headers, "cgst")
        col_sgst = _match_col(headers, "sgst")
        col_igst = _match_col(headers, "igst")

        def rate_amount(row: Dict[str, Any], col: Optional[str]) -> tuple:
            rate, amount = _split_rate_amount(_get(row, col))
            if amount is None and col is not None:
                # Rate-only cell ("@2.5%"): the amount is in the next ("AMT") column.
                nxt = headers.index(col) + 1
                if nxt < len(headers) and "amt" in _norm(headers[nxt]):
                    amount = to_float(_get(row, headers[nxt]))
            return rate, amount

        for row in rows:
            cgst_rate, cgst_amt = rate_amount(row, col_cgst)
            sgst_rate, sgst_amt = rate_amount(row, col_sgst)
            igst_rate, igst_amt = rate_amount(row, col_igst)
            slab = _get(row, col_slab)
            line = TaxLine(
                hsn_sac=_get(row, col_hsn) or (slab.strip("@ ") if slab else None),
                taxable_amount=to_float(_get(row, col_tax)),
                cgst_rate=cgst_rate,
                cgst_amount=cgst_amt,
                sgst_rate=sgst_rate,
                sgst_amount=sgst_amt,
                igst_rate=igst_rate,
                igst_amount=igst_amt,
            )
            if col_slab and (not slab or line.taxable_amount is None):
                continue  # empty slab ("12% | | |") or the column-total row
            if line.hsn_sac or line.taxable_amount is not None:
                out.append(line)
        if out:
            break
    return out


def parse_tax_summary_text(markdown: str) -> List[TaxLine]:
    """Parse GST-slab tax rows from text.

    A row starts with the slab ("12%") followed by money columns in any of the
    printed orders, e.g. ``12% 1260.22 75.62 75.62 151.24`` (taxable, SGST,
    CGST, total GST) or ``5% | 947.40 | 47.36 | 994.76 | | 23.68 | 23.68``
    (sales, total GST, total, coin, SGST, CGST). The taxable value comes first;
    SGST/CGST are the equal pair adding up to half-rate tax on it.
    """
    out: List[TaxLine] = []
    for raw in markdown.splitlines():
        line = raw.replace("|", " ").strip()
        m = re.search(r"(?<![\d.])@?\s*(\d{1,2}(?:\.\d+)?)\s*%\s+(.*)$", line)
        if not m:
            continue
        pct = float(m.group(1))
        nums = [to_float(n) for n in re.findall(r"(?<![\d.])[\d,]+\.\d{2}(?![\d.])", m.group(2))]
        if len(nums) < 3 or not pct:
            continue
        taxable, rest = nums[0], nums[1:]
        half = round(taxable * pct / 200, 2)
        pair = next(((a, b) for i, a in enumerate(rest) for b in rest[i + 1:]
                     if abs(a - b) <= 0.02 and abs(a - half) <= max(0.05, 0.02 * half)), None)
        if pair is None:
            continue
        rate = round(pct / 2, 2)
        out.append(TaxLine(hsn_sac=f"{m.group(1)}%", taxable_amount=taxable,
                           sgst_rate=rate, sgst_amount=pair[0], cgst_rate=rate, cgst_amount=pair[1]))
    return out


def _tax_details_box(invoice: Invoice) -> List[TaxLine]:
    """Single-slab "TAX DETAILS" box (Taxable / SGST Value / CGST Value) as a tax line.

    Used when no slab table was found but every line item carries the same GST %.
    """
    t = invoice.totals
    rates = {i.gst_pct for i in invoice.line_items if i.gst_pct}
    if len(rates) != 1 or None in (t.taxable_amount, t.sgst, t.cgst):
        return []
    pct = rates.pop()
    if abs(t.taxable_amount * pct / 100 - (t.sgst + t.cgst)) > max(0.1, 0.01 * t.sgst):
        return []
    label = f"{pct:g}%"
    return [TaxLine(hsn_sac=label, taxable_amount=t.taxable_amount, sgst_rate=pct / 2,
                    sgst_amount=t.sgst, cgst_rate=pct / 2, cgst_amount=t.cgst)]


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
    flat = re.sub(r"\s*\|\s*", " ", text)  # table cells joined, for "Tot. Qty: | 13"

    totals.grand_total = _amount_for(text, r"(?:grand\s*total|net\s*amount|net\s*payable)")
    totals.taxable_amount = _amount_for(
        text, r"(?:taxable\s*amount|sales\s*value|taxable\s*amt|^\W*taxable\s*(?=[:|]|$))")
    if totals.taxable_amount is None:
        totals.taxable_amount = _amount_for(text, r"^\W*(?:sub\s*total|gross\s*amount)\b")
    totals.discount = _amount_for(text, r"^\s*(?:discount|bill\s*disc)")
    totals.round_off = _amount_for(text, r"(?:round\s*off|r/?\.?\s*of|write\s*off|coin\s*\()")
    totals.cess = _amount_for(text, r"cess")
    totals.sgst = _amount_for(text, r"(?:state\s*gst|sgst)")
    totals.cgst = _amount_for(text, r"(?:central\s*gst|cgst)")
    totals.igst = _amount_for(text, r"(?:integrated\s*gst|igst)")
    totals.tax_amount = _amount_for(text, r"tax\s*amount|gst\s*total\b")

    # "TOTAL ITEM. 15 for 3" -> total_qty 15, total_items 3.
    m = re.search(r"total\s*items?\.?\s*(\d+)\s*for\s*(\d+)", flat, re.IGNORECASE)
    labelled_qty = re.search(r"\btot(?:al)?\.?\s*qty\s*[:.]?\s*(\d+(?:\.\d+)?)", flat, re.IGNORECASE)
    if m:
        totals.total_qty = to_float(m.group(1))
        totals.total_items = to_float(m.group(2))
    elif labelled_qty:  # "Tot Items : 3   Tot Qty : 12"
        totals.total_qty = to_float(labelled_qty.group(1))
        # "Tot Items : 3" anywhere, or "Tot. Qty: 13  Items 8" right after the qty.
        items = (re.search(r"\btot(?:al)?\.?\s*items?\s*[:.]?\s*(\d+)\b", flat, re.IGNORECASE)
                 or re.match(r"\s*items?\s*[:.]?\s*(\d+)\b", flat[labelled_qty.end():], re.IGNORECASE))
        if items:
            totals.total_items = to_float(items.group(1))
    else:
        # A plain "Total <qty> <amount>" row (first template).
        for line in text.splitlines():
            if re.search(r"\btotal\b", line, re.IGNORECASE) and not re.search(
                r"grand|taxable|gst|net|item|sub\s*total|value|group", line, re.IGNORECASE
            ):
                nums = [to_float(n) for n in re.findall(r"\d[\d,]*\.?\d*", line)]
                nums = [n for n in nums if n is not None]
                if nums:
                    totals.total_qty = nums[0]
                break
    return totals


_MONEY_RE = re.compile(r"-?\s*[\d,]+\.\d{2}\b")
# Start of the next "Label :" on the same line (another field's value follows it).
_NEXT_LABEL_RE = re.compile(r"\|?\s*[A-Za-z][A-Za-z .()/-]{2,}\s*:")


def _amount_for(text: str, label_pattern: str) -> Optional[float]:
    """Find the money amount (with 2 decimals) printed for ``label``.

    Takes the last amount after the label and before the next ``Label :`` on
    the line (key/value boxes put several fields on one row); when the label
    stands alone, the amount may sit in the next cell/line on its own.
    Requiring the ``.dd`` decimals avoids grabbing phone numbers, PINs or codes
    that share the line with a label in positionally-jumbled OCR text.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.search(label_pattern, line, re.IGNORECASE)
        if not m:
            continue
        segment = line[m.end():]
        segment = segment[re.match(r"(?:[ .]*[A-Za-z][A-Za-z .()/]*)?\s*:?", segment).end():]  # "SGST| Value :"
        cut = _NEXT_LABEL_RE.search(segment)
        money = _MONEY_RE.findall(segment[:cut.start()] if cut else segment)
        if not money and not cut:
            money = _MONEY_RE.findall(line)
        if money:
            return to_float(money[-1])
        for nxt in lines[i + 1:i + 3]:
            cell = nxt.strip().strip("|").strip()
            if not cell:
                continue
            if re.fullmatch(r"[₹\s]*-?\s*[\d,]+\.\d{2}", cell):
                return to_float(cell)
            break
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


_GSTIN_LOOSE_RE = re.compile(r"(?<![A-Z0-9])[0-9OIlSZB]{2}[A-Z0-9]{5}[0-9OIlSZB]{4}[A-Z0-9]{4}(?![A-Z0-9])")
_TO_DIGIT = str.maketrans("OIlSZB", "011528")
_TO_LETTER = str.maketrans("015826", "OISBZG")


def fix_gstin_ocr(text: str) -> str:
    """Repair OCR letter/digit swaps inside GSTINs by position.

    GSTIN = 2-digit state + PAN (5 letters, 4 digits, 1 letter) + entity
    digit + 'Z' + check char. "32AYXPC3443NIZ2" -> "32AYXPC3443N1Z2".
    """
    def fix(m: re.Match) -> str:
        g = m.group(0)
        if GSTIN_RE.fullmatch(g) and g[12].isdigit() and g[13] == "Z":
            return g
        c = list(g)
        c[0:2] = list("".join(c[0:2]).translate(_TO_DIGIT))
        c[2:7] = list("".join(c[2:7]).translate(_TO_LETTER))
        c[7:11] = list("".join(c[7:11]).translate(_TO_DIGIT))
        c[11] = c[11].translate(_TO_LETTER)
        c[12] = c[12].translate(_TO_DIGIT)
        if c[13] in "27":
            c[13] = "Z"
        fixed = "".join(c)
        return fixed if GSTIN_RE.fullmatch(fixed) else g
    return _GSTIN_LOOSE_RE.sub(fix, text)


def parse(markdown: str, tables: List[List[Dict[str, Any]]]) -> Invoice:
    """Map Docling markdown + tables into a structured :class:`Invoice`."""
    raw_markdown = markdown
    markdown = fix_gstin_ocr(markdown or "")
    gstins: List[str] = []
    for g in GSTIN_RE.findall((markdown or "").upper()):
        if g not in gstins:
            gstins.append(g)

    invoice = Invoice()
    invoice.seller = parse_seller(markdown, gstins)
    invoice.bill_to, invoice.ship_to = parse_parties(markdown, gstins, invoice.seller.gstin)
    if invoice.bill_to.gstin == invoice.seller.gstin:  # block grabbed the seller's GSTIN
        invoice.bill_to.gstin = next((g for g in gstins if g != invoice.seller.gstin), None)
    parse_meta(markdown, invoice)
    # Prefer Docling's recognised tables; fall back to text-based parsing
    # (e.g. the offline text backend, or templates without a clean table).
    invoice.line_items = best_line_items(markdown, tables)
    invoice.tax_summary = parse_tax_summary(tables) or parse_tax_summary_text(markdown)
    invoice.totals = parse_totals(markdown, tables)
    _reconcile_totals(invoice, markdown)
    if not invoice.tax_summary:
        invoice.tax_summary = _tax_details_box(invoice)
    invoice.purchase_lines = build_purchase_lines(invoice.line_items)
    invoice.raw = RawOutput(markdown=raw_markdown, tables=tables)
    return invoice


def _reconcile_totals(invoice: Invoice, full_text: str = "") -> None:
    """Fill obvious gaps in totals from the tax summary / line items."""
    t = invoice.totals
    if t.taxable_amount is not None and t.sgst is None and t.cgst is None:
        # Column-total row of a slab table: "1960.79 | 229.76 | 2190.55 | 114.88 | 114.88"
        for line in full_text.splitlines():
            nums = [to_float(x) for x in _MONEY_RE.findall(line)]
            if len(nums) < 3 or abs(nums[0] - t.taxable_amount) > 0.01:
                continue
            pair = next((a for i, a in enumerate(nums[1:], 1) for b in nums[i + 1:]
                         if a == b and any(abs(2 * a - n) <= 0.02 for n in nums)), None)
            if pair:
                t.sgst = t.cgst = pair
                break
    # Only trust per-slab GST sums when the summary covers the whole taxable amount.
    slab_taxable = sum(x.taxable_amount or 0 for x in invoice.tax_summary)
    complete = (t.taxable_amount is not None and abs(slab_taxable - t.taxable_amount) < 0.05)
    for key in ("sgst", "cgst", "igst"):
        if complete and getattr(t, key) is None:
            amounts = [getattr(x, f"{key}_amount") for x in invoice.tax_summary]
            if any(a is not None for a in amounts):
                setattr(t, key, round(sum(a for a in amounts if a is not None), 2))
    if invoice.totals.taxable_amount is None:
        tax_sum = sum(t.taxable_amount for t in invoice.tax_summary
                      if t.taxable_amount is not None)
        if tax_sum:
            invoice.totals.taxable_amount = round(tax_sum, 2)
        else:
            item_sum = sum(i.value for i in invoice.line_items if i.value is not None)
            if item_sum:
                invoice.totals.taxable_amount = round(item_sum, 2)
    t = invoice.totals
    tax = t.tax_amount
    if tax is None and None not in (t.sgst, t.cgst):
        tax = round(t.sgst + t.cgst + (t.igst or 0) + (t.cess or 0), 2)
    if t.taxable_amount is not None and tax is not None:
        payable = t.taxable_amount + tax - (t.discount or 0)
        if t.grand_total is None and full_text:
            # Net payable is printed rounded to the rupee near taxable + tax.
            near = [v for v in (to_float(x) for x in _MONEY_RE.findall(full_text))
                    if v is not None and abs(v - payable) <= 1.0]
            if near:
                t.grand_total = sorted(near, key=lambda v: (not float(v).is_integer(), abs(v - payable)))[0]
        if t.round_off is None and t.grand_total is not None and abs(t.grand_total - payable) < 1.0:
            diff = round(t.grand_total - payable, 2)
            t.round_off = diff if diff else None
    if invoice.totals.total_qty is None and invoice.line_items:
        qty_sum = sum(i.qty for i in invoice.line_items if i.qty is not None)
        if qty_sum:
            invoice.totals.total_qty = qty_sum
