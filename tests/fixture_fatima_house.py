"""Docling-shaped output for examples/fatima_house_invoice.jpg (BlueFox template).

Fatima Drug House invoice 127191: unlabelled customer block, "Tot Items / Tot Qty"
totals, "Write Off" round-off and a GST-slab summary table without an HSN column.
Used by the tests and by generate_sample_output.py.
"""

MARKDOWN = """*127191*
TAX INVOICE
Page 1 of 1
FATIMA DRUG HOUSE
DOOR NO:8/1457A&B, CALICUT ROAD,
MALAPPURAM, MANJERI
MANJERI- 676121
DL No : KL-MLP-149329-20B, KL-MLP-149330-21B
PH : 8589009924 , 04832111000 , 8589009927
GSTIN : 32AFJPK9601F2ZN
PAN : AFJPK9601F
FSSAI : 11323010001083
MSME No. : 09-0010201
Kerala-32 E-Mail : fdhmnji@yahoo.in
13172 - ROSE MEDICALS
NEW BUS STAND, EDAKKARA
EDAKKARA,PIN:679331- 679331
Phone : 8547719594 ,
GSTIN : 32AYXPC3443N1Z2
MSME No. : 32100955031
DL No : KL-MLP-141950, KL-MLP-141951
Local
Pay Type : CREDIT
FSSAI : 11323010001083
Mail Id : rosemedicals999@gmail.com
State : Kerala-32, District : MALAPPURAM
Route :
Inv No: 127191
Inv Dt: 28/11/2025 4:53 PM
Order No & Dt :
Excecutive : 18 - NIYAS MON P
Exec.Mob No. : 7907954969
Description :
Remarks : *NOW AVAILABLE CIPLA ALL DIVISIONS,GLAXO, MEYER ORGANICS,VANGUARD THERAP
Tot Items : 3 Tot Qty : 12
Sub Total : 1580.15
Cash Disc (Item) : 0.00
Scheme Disc (Item) :
Taxable Amount : 1580.15
Tax Amount : 79.01
Bill Disc. : 0.00
TDS / TCS Amount : 0.00
Write Off : -0.16
Net Amount : 1659.00
CreditNote Amt. : 0.00
DebitNote Amt. : 0.00
Net Payable : 1659.00
Amount In Words : One Thousand Six Hundred and Fifty Nine Rupees Only
Prep By: RENJITH
Print By: FDHSTORE
Print On:28/11/2025 5:00 P
For FATIMA DRUG HOUSE
Authorised Signatory
"""

H = ["SNo", "Rack", "Mfac", "Particulars", "Packing", "HSN", "BatchNo", "Exp", "Qty", "Sch Qty",
     "MRP", "Rate", "Sch. Disc", "Disc %", "GST %", "Taxable Value"]
ROWS = [
    ["1", "A139", "ZYDUS", "ATORVA 40 TAB", "10'S", "30049079", "IA01199A", "07/28", "5", "", "206.18", "157.09", "0.00", "", "5", "785.45"],
    ["2", "A143", "ZYDUS", "ATORVA GOLD 20", "15'S", "30049079", "25S2GCA743", "01/27", "4", "", "145.45", "110.82", "0.00", "", "5", "443.28"],
    ["3", "J168", "BLUE (", "K-GLIM TRIO 2 MG", "15'S", "30043190", "ZAFQ2512", "04/27", "1", "", "153.75", "117.14", "0.00", "", "5", "117.14"],
    ["4", "J168", "BLUE (", "K-GLIM TRIO 2 MG", "15'S", "30043190", "ZAFQ2513", "05/27", "2", "", "153.75", "117.14", "0.00", "", "5", "234.28"],
]
TAX = [{"Tax %": "@5%", "Taxable value": "1580.15", "SGST %": "@2.5%", "AMT": "39.50", "CGST %": "@2.5%", "AMT_2": "39.50"}]
TABLES = [[dict(zip(H, r)) for r in ROWS], TAX]
