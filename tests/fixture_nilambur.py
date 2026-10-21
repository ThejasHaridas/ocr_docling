"""Docling-shaped output for three photographed purchase bills (Rose Medicals, Nov 2025).

Transcribed from the photos: plain text blocks one per line, tables as
markdown pipe rows. They reproduce what broke on the live Docling run:

* KOTTAKUTH - merged header cells ("HSNCODEGST%", "EXPD.PACKQTY") so Docling's
  table has values under the wrong columns; "TAX DETAILS" key/value box.
* SURYA     - seller GSTIN printed without a label; no item table recognised;
  grand total amount on the line after its label.
* MALABAR   - buyer's GSTIN box read before the "To" label; slab tax table
  (Gst% | Sales | TOTAL GST | ...); one qty hidden under a tick mark.
"""

KOTTAKUTH_MD = """## GST INVOICE
## KOTTAKUTH DRUGS & SURGICALS
Pharmaceutical Distributor.
P.K.Building,Classic College Road,
NILAMBUR, MALAPPURAM Dt
Ph:9497809980,9446853980
DL.No : WLF20B2024KL002059,WLF21B2024KL002000
GST : 32AEEPM3866Q2ZY
kottakuthdrugs123@gmail.com
To. ROSE MEDICALS
EDAKKARA
Ph : 277222
DL.No.: KL-MLP-141950/20,141951/21/19
GST No: 32AYXPC3443N1Z2
Inv.No : 2751
Date : 25/11/2025
CREDIT BILL
| DESCRIPTION. | HSNCODEGST% | MANF. | BATCH | EXPD.PACKQTY | FR. | RATE | Disc% | M.R.P | AMOUNT. |
|---|---|---|---|---|---|---|---|---|---|
| UNDERPAD MAMMIES | 96190090 5.0 | HYGI | AMIY24 | 8 /29 10'S 1 | | 165.00 | | 656.25 | 165.00 |
| UNDERPAD MAMMIES | 96190090 5.0 | HYGI | AA008 | 5 /28 10'S 2 | | 165.00 | | 656.00 | 330.00 |
GROUP TOTAL GST 5.0 % 24.75
Group Total Value: 495.00
TAX DETAILS
| Taxable : | 495.00 | GST TOTAL : | 24.75 |
| Non.Txable : | 0.00 | SGST Value : | 12.38 |
| MRP.Value | 0.00 | CGST Value: | 12.38 |
Cess : 0.00
Original/Duplicate.
GRAND TOTAL : 520.00
(Rupees five hundred twenty only)
Subject to Nilambur jurisdiction.
For KOTTAKUTH DRUGS & SURGICALS
OUR BANK:
FEDERAL BANK,NILAMBUR, A/C NO:11565500014237, IFSC:FDRL0001156
Authorised Signatory.
Page :1
"""

# What Docling's TableFormer handed over: values shifted under merged headers.
KOTTAKUTH_TABLES = [[
    {"DESCRIPTION.": "UNDERPAD MAMMIES", "HSNCODEGST%": "5.0", "MANF.": "HYGI", "BATCH": "8 /29",
     "EXPD.PACKQTY": "10'S", "FR.": "1", "RATE": "", "Disc%": "165.00", "M.R.P": "165.00", "AMOUNT.": "656.25"},
    {"DESCRIPTION.": "UNDERPAD MAMMIES", "HSNCODEGST%": "06006196", "MANF.": "5.0 HYGI", "BATCH": "AA008",
     "EXPD.PACKQTY": "5 /28 10'S", "FR.": "2", "RATE": "852810", "Disc%": "2165.00", "M.R.P": "330.00",
     "AMOUNT.": ""},
]]

SURYA_MD = """GST INVOICE
## SURYA PHARMA
Pharmaceutical Distributors
Door No:N.M.26/223,V.K Road
Opp.Rajeswary Theatre, NILAMBUR, MALAPPURAM
P.O BOX-679329, Email:suryapharma052@gmail.com
Ph:8089621889(office),9400860813
32GDHPP8475E1Z7
DL.No :KL-MLP-152564-KL-MLP-152565/02/03/2021
To. ROSE MEDICALS
NEW BUS STAND
EDAKKARA
Ph : 8547719594
DL.No.: KL-MLP-141950/141951
GST No: 32AYXPC3443N1Z2
Inv.No : 2649
Date : 25/11/2025
CREDIT BILL
DESCRIPTION. HSN GST% MANF/MARI BATCH EXPD. PACK QTY FR. RATE Disc% M.R.P AMOUNT.
STIM ORTHO MB - 96032100 5.0 DENTA DMB-10-25 9 /30 1'S 12 74.58 98.00 894.96
UNDERPADS-60X90 -TIDY 961900 5.0 PIONEER CD54613JA 9 /28 10'S 2 162.50 1450.00 325.00
GROUP TOTAL GST: 5.0 % 61.00
TAX DETAILS
| Taxable : | 1,219.96 | GST TOTAL : | 61.00 |
| Non.Txable : | 0.00 | SGST Value : | 30.50 |
| MRP.Value | 0.00 | CGST Value: | 30.50 |
Cess : 0.00
Original/Duplicate.
GRAND TOTAL :
1,281.00
(Rupees one thousand two hundred eighty one only)
Subject to . jurisdiction.
For SURYA PHARMA
Authorised Signatory.
SIB NILAMBUR A/C:0057073000001443 IFSC:SIBL0000057
Page :1
"""

MALABAR_MD = """Page 1 of 1
GST INVOICE
## MALABAR DRUG LINES
For Order Whats App: 7902735374
XV111/923-CHEMIST BHAVAN
A.K ROAD,DOWN HILL
MALAPPURAM-676519
0483 2735374-2730374-2730380-2730381
DL No. : WLF20B2024KL000287
DL No. : WLF21B2024KL000280
GST No.: 32AADFM8699L1ZM
DL NO: KL-MLP-141950/20/2019
DL NO: KL-MLP-141951/21/2019
GST NO: 32AYXPC3443N1Z2
To
ROSE MEDICALS---
EDAKKARA---
EDAKKARA
BILL No : HM32622
BILL Date : 25/11/2025
Terms : CREDIT
| HSN | MFR | BATCH | EX.DT | M.R.P | DESCRIPTION | PKG. | QTY | RATE | GST% | AMOUNT |
|---|---|---|---|---|---|---|---|---|---|---|
| 30049029 | CANBR | E0111 | 02-27 | 140.59 | DANZOL SHAMPOO 60ML | 1X60ML | 2 | 107.97 | 5 | 215.94 |
| 30049069 | EXMO | LTA-48476 | 05-28 | 56.20 | EXDOL-P ## | 1X10 | 2 | 42.82 | 5 | 85.64 |
| 30049081 | CIPLA | 5SA1590 | 06-27 | 217.36 | LAMETEC 50 TAB | 1X10 | 2 | 165.61 | 5 | 331.22 |
| 33049930 | AUREL | AD5F12 | 05-27 | 240.00 | MOISOFT CLEANSING LOTION | 1X125M | 1 | 169.49 | 18 | 169.49 |
| 30049099 | GARY | 44 | 06-27 | 140.63 | TRIOLITE CREAM 15 GM | 1X15 G | 2 | 111.05 | 5 | 222.10 |
| 33049930 | AUREL | AD5H17 | 07-27 | 465.00 | UVedge SPF 50 SUNSCREEN GEL | 1X50GR | | 328.38 | 18 | 656.76 |
| 33049930 | AUREL | AD5E16 | 04-27 | 265.00 | UVEDGE SPF30 SUN LOTION | 1X50ML | 1 | 187.14 | 18 | 187.14 |
| 30049029 | GLEN | 46250094 | 02-27 | 121.41 | SCABOMA LOTION 100ML | 1X100 | 1 | 92.50 | 5 | 92.50 |
Gross Amount 1960.79
Sub Total 1960.79
| Gst% | Sales | TOTAL GST | TOTAL | Coin(+/-) | SGST | CGST | IGST |
|---|---|---|---|---|---|---|---|
| 0% | | | | 0.45 | | | |
| 5% | 947.40 | 47.36 | 994.76 | | 23.68 | 23.68 | |
| 12% | | | | CESS: | | | |
| 18% | 1013.39 | 182.40 | 1195.79 | 0.00 | 91.20 | 91.20 | |
| 28% | | | | | | | |
| | 1960.79 | 229.76 | 2190.55 | | 114.88 | 114.88 | |
RUPEES TWO THOUSAND ONE HUNDRED NINETY-ONE ONLY
NET AMOUNT` 2191.00
Rep 02 Tot. Qty: 13 Items 8 Time: 14:31
For MALABAR DRUG LINES
(Authorised Signatory)
E.& O.E Subject to Malappuram Jurisdiction
"""
