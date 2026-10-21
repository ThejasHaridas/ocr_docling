"""Tests for the purchase-invoice column mapping.

Tables below mirror what Docling's TableFormer yields for the photographed
supplier invoices (header row -> list of row dicts), so the header-synonym
matching and the pack/unit maths are exercised without needing OCR models.
Run with: python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bill_extractor.column_mapping import COLUMNS, OCR_HEADERS, coverage
from bill_extractor.parser import parse, parse_line_items
from bill_extractor.purchase import infer_drug_form, parse_pack_size

# Chem Pharma / Fatima Drug Lines layout (invoice C25-73799).
CHEM = [
    {"MFR": "SUN", "HSN": "30049099", "DESCRIPTION": "LIOFEN 10 TAB *", "PKG.": "10's",
     "BATCH": "SIG0215A", "EX.DT": "12-26", "M.R.P": "124.84", "QTY": "2", "RATE": "95.12",
     "DIS%": "", "Amount": "190.24", "GST %": "5", "GST AMT": "9.52", "Net Amt.": "199.76"},
    {"MFR": "GLAXO", "HSN": "30043990", "DESCRIPTION": "ELTROXIN 100 TAB", "PKG.": "120's",
     "BATCH": "MW8W", "EX.DT": "01-27", "M.R.P": "168.84", "QTY": "2", "RATE": "128.64",
     "DIS%": "", "Amount": "257.28", "GST %": "5", "GST AMT": "12.86", "Net Amt.": "270.14"},
]

# Fatima Drug Lines F25-74317: scheme stock printed as a second "Free" row.
FREE = [
    {"MFR": "DABUR", "HSN": "30049011", "DESCRIPTION": "PUDIN HARA CAP", "PKG.": "10's",
     "BATCH": "BD03224", "EX.DT": "05-27", "M.R.P": "32.81", "QTY": "9", "RATE": "28.93",
     "DIS%": "", "Amount": "260.37", "GST %": "5", "GST AMT": "13.02", "Net Amt.": "273.39"},
    {"MFR": "DABUR", "HSN": "30049011", "DESCRIPTION": "PUDIN HARA CAP", "PKG.": "10's",
     "BATCH": "BD03224", "EX.DT": "05-27", "M.R.P": "32.81", "QTY": "1", "RATE": "Free",
     "DIS%": "", "Amount": "", "GST %": "5", "GST AMT": "", "Net Amt.": ""},
]

# Bluefox layout (Fatima Healthcare 66281): "Sch Qty" must not win over "Qty".
BLUEFOX = [
    {"SNo": "1", "Rack": "A104", "Mfac": "EYSYS", "Particulars": "GLISIMET M2", "Packing": "14'S",
     "HSN": "30049099", "BatchNo": "SGY0077", "Exp": "01/27", "Qty": "4", "Sch Qty": "x",
     "MRP": "104.36", "Rate": "79.51", "Sch. Disc": "0.00", "Disc %": "", "GST %": "5",
     "Taxable Value": "318.04"},
]

# Kurikkal layout: explicit discount amount and a line total incl. GST.
KURIKKAL = [
    {"S.No": "1", "HSN/SAC": "30045090", "MFR": "EXMO", "Products": "EXFER-Z TAB", "Pack": "10'S",
     "Qty": "2", "Batch": "LTA-50390F", "Expiry": "09/27", "M.R.P": "83.400", "Rate": "63.540",
     "Disc. Amt": "0.00(0.00)", "Sch. Disc.": "0.00(0.00)", "Amount": "127.08", "GST%": "5.00",
     "GST Amt": "6.35", "Total": "133.44"},
]


class PackAndForm(unittest.TestCase):
    def test_pack_size(self):
        cases = {"10's": 10, "15s": 15, "14'S": 14, "1X10": 10, "1'S": 1, "120's": 120,
                 "30Gr": 1, "100 ML": 1, "1X60ML": 1, "250gm": 1, "1kit": 1, "": None, None: None}
        for label, expected in cases.items():
            self.assertEqual(parse_pack_size(label), expected, label)

    def test_drug_form(self):
        self.assertEqual(infer_drug_form("LIOFEN 10 TAB *"), "Tablet")
        self.assertEqual(infer_drug_form("Lycor 1.0% Cream"), "Cream")
        self.assertEqual(infer_drug_form("LEEDS SYRUP"), "Syrup")
        self.assertIsNone(infer_drug_form("ENSURE VANILA 950GM"))


class ChemPharmaLayout(unittest.TestCase):
    def setUp(self):
        self.inv = parse("INVOICE\nCHEM PHARMA\n", [CHEM])
        self.line = self.inv.purchase_lines[0]

    def test_line_item_columns(self):
        li = self.inv.line_items[0]
        self.assertEqual((li.mfr, li.pack, li.batch, li.expiry), ("SUN", "10's", "SIG0215A", "12-26"))
        self.assertEqual((li.qty, li.rate, li.gst_pct, li.gst_amount), (2.0, 95.12, 5.0, 9.52))
        self.assertEqual((li.value, li.net_amount), (190.24, 199.76))

    def test_pack_and_unit_maths(self):
        ln = self.line
        self.assertEqual((ln.packSize, ln.packs, ln.quantity, ln.totalQuantity), (10, 2.0, 20.0, 20.0))
        self.assertEqual((ln.mrpPerPack, ln.mrp), (124.84, 12.484))
        self.assertEqual((ln.ratePerPack, ln.purchaseRate), (95.12, 9.512))

    def test_amounts_and_tax(self):
        ln = self.line
        self.assertEqual((ln.grossAmount, ln.purchaseAmount), (190.24, 190.24))
        self.assertEqual((ln.taxPercent, ln.taxAmount, ln.billedAmount), (5.0, 9.52, 199.76))
        self.assertEqual((ln.cgstAmount, ln.sgstAmount), (4.76, 4.76))
        self.assertEqual((ln.landingCostPerPack, ln.landingCostPerUnit), (95.12, 9.512))
        self.assertEqual(ln.expiryDate, "12/26")
        self.assertEqual(ln.drugForm, "Tablet")

    def test_120_pack(self):
        self.assertEqual(self.inv.purchase_lines[1].packSize, 120)


class FreeRows(unittest.TestCase):
    def test_free_row_merges_into_scheme(self):
        items = parse_line_items([FREE])
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].qty, items[0].free_qty), (9.0, 1.0))
        ln = parse([""][0], [FREE]).purchase_lines[0]
        self.assertEqual((ln.packs, ln.freePacks, ln.scheme), (9.0, 1.0, "9 + 1"))
        self.assertEqual((ln.quantity, ln.freeQuantity, ln.totalQuantity), (90.0, 10.0, 100.0))
        self.assertEqual(ln.landingCostPerPack, round(260.37 / 10, 2))


class OtherLayouts(unittest.TestCase):
    def test_bluefox_qty_not_sch_qty(self):
        ln = parse("", [BLUEFOX]).purchase_lines[0]
        self.assertEqual((ln.packs, ln.packSize, ln.quantity), (4.0, 14, 56.0))
        self.assertEqual((ln.purchaseAmount, ln.taxPercent, ln.taxAmount, ln.billedAmount),
                         (318.04, 5.0, 15.9, 333.94))
        self.assertEqual(ln.sequenceNumber, 1)

    def test_kurikkal_total_column(self):
        li = parse_line_items([KURIKKAL])[0]
        self.assertEqual((li.value, li.net_amount, li.gst_amount), (127.08, 133.44, 6.35))


class OcrHeaders(unittest.TestCase):
    """Every header in the mapping sheet's "OCR" column is extracted."""

    # PurchaseLine field filled from each OCR column, with the sample value below.
    EXPECTED = {4: ("itemName", "AMOXIL 500 CAP"), 10: ("manufacturer", "GSK"),
                17: ("hsnCode", "30041010"), 22: ("packSize", 10), 24: ("batch", "AMX2401"),
                26: ("expiryDate", "03/28"), 31: ("packs", 50.0), 36: ("mrpPerPack", 120.0),
                38: ("ratePerPack", 77.14), 43: ("discountPercent", 5.0),
                49: ("purchaseAmount", 3664.15), 50: ("taxPercent", 12.0),
                54: ("billedAmount", 4103.85)}
    VALUES = {4: "AMOXIL 500 CAP", 10: "GSK", 17: "30041010", 22: "10's", 24: "AMX2401",
              26: "03-28", 31: "50", 36: "120.00", 38: "77.14", 43: "5", 49: "3664.15",
              50: "12", 54: "4103.85"}

    def test_every_spelling(self):
        self.assertEqual(set(OCR_HEADERS), set(self.EXPECTED))
        width = max(len(v) for v in OCR_HEADERS.values())
        for i in range(width):
            # One table per spelling variant; columns with fewer variants reuse the first.
            row = {hs[min(i, len(hs) - 1)]: self.VALUES[no] for no, hs in OCR_HEADERS.items()}
            ln = parse("", [[row]]).purchase_lines
            self.assertEqual(len(ln), 1, list(row))
            for no, (field, expected) in self.EXPECTED.items():
                self.assertEqual(getattr(ln[0], field), expected, (no, list(row)))

    def test_each_header_alone(self):
        from bill_extractor.parser import _resolve_columns
        for no, headers in OCR_HEADERS.items():
            for h in headers:
                self.assertTrue(any(_resolve_columns([h]).values()), (no, h))


class BlueFoxFatimaHouse(unittest.TestCase):
    """examples/fatima_house_invoice.jpg (Fatima Drug House, invoice 127191)."""

    def setUp(self):
        from fixture_fatima_house import MARKDOWN, TABLES
        self.inv = parse(MARKDOWN, TABLES)

    def test_header(self):
        inv = self.inv
        self.assertEqual((inv.invoice_no, inv.invoice_date), ("127191", "28/11/2025"))
        self.assertEqual((inv.seller.name, inv.seller.gstin), ("FATIMA DRUG HOUSE", "32AFJPK9601F2ZN"))
        self.assertEqual(inv.seller.fssai, "11323010001083")
        self.assertEqual((inv.bill_to.name, inv.bill_to.gstin, inv.bill_to.pin),
                         ("ROSE MEDICALS", "32AYXPC3443N1Z2", "679331"))

    def test_lines(self):
        lines = self.inv.purchase_lines
        self.assertEqual([ln.batch for ln in lines], ["IA01199A", "25S2GCA743", "ZAFQ2512", "ZAFQ2513"])
        self.assertEqual([ln.packs for ln in lines], [5.0, 4.0, 1.0, 2.0])
        self.assertEqual([ln.packSize for ln in lines], [10, 15, 15, 15])
        self.assertEqual(round(sum(ln.purchaseAmount for ln in lines), 2), 1580.15)
        self.assertEqual((lines[0].mrpPerPack, lines[0].ratePerPack, lines[0].taxPercent),
                         (206.18, 157.09, 5.0))
        self.assertIsNone(lines[0].freePacks)  # "Sch Qty" is blank, not the qty

    def test_totals_and_tax(self):
        t = self.inv.totals
        self.assertEqual((t.total_items, t.total_qty), (3.0, 12.0))
        self.assertEqual((t.taxable_amount, t.sgst, t.cgst, t.tax_amount), (1580.15, 39.5, 39.5, 79.01))
        self.assertEqual((t.round_off, t.grand_total), (-0.16, 1659.0))
        tax = self.inv.tax_summary
        self.assertEqual([(x.hsn_sac, x.sgst_rate, x.sgst_amount) for x in tax], [("5%", 2.5, 39.5)])


class NilamburBills(unittest.TestCase):
    """Kottakuth / Surya / Malabar photos where the live Docling run lost data."""

    @classmethod
    def setUpClass(cls):
        import fixture_nilambur as F
        cls.kottakuth = parse(F.KOTTAKUTH_MD, F.KOTTAKUTH_TABLES)
        cls.surya = parse(F.SURYA_MD, [])
        cls.malabar = parse(F.MALABAR_MD, [])

    def test_seller_and_buyer_not_swapped(self):
        for inv, name, gstin in ((self.kottakuth, "KOTTAKUTH DRUGS & SURGICALS", "32AEEPM3866Q2ZY"),
                                 (self.surya, "SURYA PHARMA", "32GDHPP8475E1Z7"),
                                 (self.malabar, "MALABAR DRUG LINES", "32AADFM8699L1ZM")):
            self.assertEqual((inv.seller.name, inv.seller.gstin), (name, gstin))
            self.assertEqual((inv.bill_to.name, inv.bill_to.gstin), ("ROSE MEDICALS", "32AYXPC3443N1Z2"))
        self.assertEqual(self.kottakuth.bill_to.dl_no, "KL-MLP-141950/20,141951/21/19")
        self.assertIsNone(self.kottakuth.bill_to.pin)  # "Ph : 277222" is a landline

    def test_invoice_numbers(self):
        self.assertEqual([(i.invoice_no, i.invoice_date) for i in (self.kottakuth, self.surya, self.malabar)],
                         [("2751", "25/11/2025"), ("2649", "25/11/2025"), ("HM32622", "25/11/2025")])

    def test_totals(self):
        k, s, m = self.kottakuth.totals, self.surya.totals, self.malabar.totals
        self.assertEqual((k.taxable_amount, k.sgst, k.tax_amount, k.grand_total, k.round_off, k.total_qty),
                         (495.0, 12.38, 24.75, 520.0, 0.25, 3.0))
        self.assertEqual((s.taxable_amount, s.sgst, s.grand_total, s.round_off), (1219.96, 30.5, 1281.0, 0.04))
        self.assertEqual((m.taxable_amount, m.sgst, m.cgst, m.grand_total, m.round_off, m.total_qty, m.total_items),
                         (1960.79, 114.88, 114.88, 2191.0, 0.45, 13.0, 8.0))

    def test_tax_summary(self):
        self.assertEqual([(t.hsn_sac, t.taxable_amount, t.sgst_amount) for t in self.malabar.tax_summary],
                         [("5%", 947.4, 23.68), ("18%", 1013.39, 91.2)])
        self.assertEqual([(t.hsn_sac, t.taxable_amount, t.cgst_amount) for t in self.kottakuth.tax_summary],
                         [("5%", 495.0, 12.38)])

    def test_misaligned_table_falls_back_to_row_text(self):
        rows = [(li.hsn_code, li.mfr, li.batch, li.expiry, li.pack, li.qty, li.rate, li.mrp, li.value)
                for li in self.kottakuth.line_items]
        self.assertEqual(rows, [("96190090", "HYGI", "AMIY24", "8/29", "10'S", 1.0, 165.0, 656.25, 165.0),
                                ("96190090", "HYGI", "AA008", "5/28", "10'S", 2.0, 165.0, 656.0, 330.0)])

    def test_rows_without_table(self):
        s = [(li.description, li.hsn_code, li.batch, li.qty, li.rate, li.mrp, li.value)
             for li in self.surya.line_items]
        self.assertEqual(s, [("STIM ORTHO MB", "96032100", "DMB-10-25", 12.0, 74.58, 98.0, 894.96),
                             ("UNDERPADS-60X90 -TIDY", "961900", "CD54613JA", 2.0, 162.5, 1450.0, 325.0)])
        m = self.malabar.line_items
        self.assertEqual(len(m), 8)
        self.assertEqual(round(sum(li.value for li in m), 2), 1960.79)
        self.assertEqual((m[5].description, m[5].qty, m[5].gst_pct), ("UVedge SPF 50 SUNSCREEN GEL", 2.0, 18.0))
        self.assertEqual([li.mfr for li in m][:3], ["CANBR", "EXMO", "CIPLA"])

    def test_gstin_ocr_repair(self):
        from bill_extractor.parser import fix_gstin_ocr
        self.assertEqual(fix_gstin_ocr("GST No: 32AYXPC3443NIZ2"), "GST No: 32AYXPC3443N1Z2")
        self.assertEqual(fix_gstin_ocr("GSTNo:32AYXPC3443N122"), "GSTNo:32AYXPC3443N1Z2")
        self.assertEqual(fix_gstin_ocr("32AEEPM3866Q2ZY"), "32AEEPM3866Q2ZY")


class Catalog(unittest.TestCase):
    def test_catalog_matches_pdf_summary(self):
        self.assertEqual(len(COLUMNS), 70)
        statuses = [c.status for c in COLUMNS]
        self.assertEqual((statuses.count("Mapped"), statuses.count("Partial"),
                          statuses.count("Missing")), (31, 11, 28))
        self.assertEqual([c.no for c in COLUMNS], list(range(1, 71)))

    def test_coverage(self):
        rows = {r["field"]: r for r in coverage(parse("", [CHEM]).purchase_lines)}
        self.assertEqual(rows["batch"]["filled"], 2)
        self.assertEqual(rows["freePacks"]["filled"], 0)


if __name__ == "__main__":
    unittest.main()
