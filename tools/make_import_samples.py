"""Build the synthetic sample files for bulk import (Sprint 15).

    python3 tools/make_import_samples.py            # writes tests/fixtures/import/

Every file here is made-up data shaped like a real source (an expense-app
export, bank statements, a Word table, a scanned PDF). Nothing personal.
Needs openpyxl and Pillow; xlwt (dev only) for the legacy .xls file.
"""
import csv
import io
import os
import random
import sys
import zipfile
from datetime import datetime, timedelta

OUT = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "import")
random.seed(15)

SHOPS = [
    ("Food", "Eating out", "Cafe Mocha - dinner"), ("Food", "Beverages", "Tea"),
    ("Household", "Kitchen", "Groceries - Big Bazaar"), ("Transportation", "Flight", "Flight to Delhi"),
    ("Health", "Medicine", "Apollo pharmacy"), ("Subscriptions", "", "Music subscription"),
    ("Social Life", "Fun", "Bowling with friends"), ("Apparel", "Laundry", "Laundry"),
    ("Gift", "", "Birthday gift"), ("Grandma", "", "Phone bill - Grandma"),
]
ACCOUNTS = ["KOTAK Credit Card", "HDFC Card", "HDFC Credit Card", "Cash"]


def out(name):
    return os.path.join(OUT, name)


# ---- 1. Expense-app export (same 12-column shape as a Money Manager export) ----
def money_manager_rows():
    rows, t = [], datetime(2026, 10, 31, 20, 0, 0)
    for i in range(31):
        cat, sub, note = SHOPS[i % len(SHOPS)]
        acc = ACCOUNTS[i % len(ACCOUNTS)]
        amt = float(random.choice([20, 65, 185, 299, 320, 389, 947, 1135, 1795, 2300, 13428, 17731]))
        if i % 9 == 4:                      # two rows with the very same timestamp
            pass
        else:
            t -= timedelta(hours=random.randint(10, 30))
        if i == 30:                         # a datetime with no time part
            t = datetime(2026, 10, 2)
        conv = amt if acc != "Cash" else round(amt / 75, 2)   # 2nd amount column disagrees on Cash
        kind = "Income" if i in (7, 21) else "Expense"
        rows.append([t, acc, cat, sub, note + ("'s" if i == 12 else ""), amt, kind, "", "",
                     str(amt), "INR", conv])
    return rows


def write_money_manager():
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Money Manager"
    ws.append(["Date", "Account", "Category", "Subcategory", "Note", "INR", "Income/Expense",
               "Tag", "Description", "Amount", "Currency", "Account"])
    for r in money_manager_rows():
        ws.append(r)
    for c in ws["A"][1:]:
        c.number_format = "dd/MM/yyyy HH:mm:ss"
    wb.save(out("money-manager-style.xlsx"))


# ---- 2. Bank statement with junk rows above the header (HDFC-like), legacy .xls ----
def bank_rows():
    rows, d = [], datetime(2026, 10, 1)
    bal = 54000.0
    for i in range(18):
        d += timedelta(days=random.randint(0, 2))
        wd = random.choice([0, 0, 250.0, 1299.0, 4500.5, 89.0, 18000.0])
        dep = 0.0 if wd else random.choice([5000.0, 85000.0, 1200.0])
        bal = bal - wd + dep
        narr = random.choice(["UPI-SWIGGY-swiggy@okaxis-ORDER", "POS 4521XXXX AMAZON", "NEFT CR-SALARY ACME LTD",
                              "UPI-RAVI KUMAR-ravi@oksbi-PAYMENT", "ATM WDL MG ROAD", "ACH D- LIC PREMIUM"])
        rows.append([d.strftime("%d/%m/%y"), narr, f"{900000 + i:010d}", d.strftime("%d/%m/%y"),
                     wd or "", dep or "", round(bal, 2)])
    return rows


def write_bank_xls():
    try:
        import xlwt
    except ImportError:
        print("  (skipped hdfc-style.xls: pip install xlwt to build it)")
        return
    wb = xlwt.Workbook(); ws = wb.add_sheet("Statement")
    junk = ["SAMPLE BANK LTD", "Statement of account", "Account Branch : MG ROAD", "Address : 1 Example Street",
            "", "City : BENGALURU", "Phone no. : 0000000000", "OD Limit : 0.00", "Currency : INR",
            "Email : xxxxxxx@example.com", "Cust ID : 00000000", "Account No : XXXXXXXX1234",
            "A/C Open Date : 01/01/20", "Account Status : Regular", "RTGS/NEFT IFSC : SBNK0000001",
            "MICR : 000000000", "Branch Code : 0001", "Product Code : 100", "", "Statement From : 01/10/2026 To : 31/10/2026"]
    for i, line in enumerate(junk):
        ws.write(i, 0, line)
    hdr = ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
    r0 = len(junk)
    ws.write(r0, 0, "*" * 30)
    for j, h in enumerate(hdr):
        ws.write(r0 + 1, j, h)
    for i, row in enumerate(bank_rows()):
        for j, v in enumerate(row):
            ws.write(r0 + 2 + i, j, v)
    ws.write(r0 + 3 + len(bank_rows()), 0, "STATEMENT SUMMARY :-")
    wb.save(out("hdfc-style.xls"))


# ---- 3. CSV variants ----
def write_csvs():
    # Debit / Credit columns, Indian grouping, Dr/Cr words (Kotak/ICICI-like).
    with open(out("kotak-style.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Sl. No.", "Transaction Date", "Value Date", "Description", "Chq / Ref number", "Debit", "Credit", "Balance", "Dr / Cr"])
        bal = 120000
        for i in range(12):
            deb = random.choice(["1,250.00", "", "45,000.00", "320.50", ""])
            cre = "" if deb else random.choice(["1,00,000.00", "2,500.00"])
            w.writerow([i + 1, f"{i + 3:02d}-10-2026", f"{i + 3:02d}-10-2026",
                        random.choice(["UPI/DR/123/ZOMATO", "IMPS/CR/RENT REFUND", "BIL/ONL/ELECTRICITY"]),
                        f"REF{i:05d}", deb, cre, f"{bal:,}", "DR" if deb else "CR"])
    # Splitwise-style: Date,Description,Category,Cost,Currency + people columns.
    with open(out("splitwise.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Description", "Category", "Cost", "Currency", "You", "Neha"])
        for i, (desc, cost) in enumerate([("Dinner", "1800.00"), ("Cab to airport", "950.00"), ("Groceries", "2340.50")]):
            w.writerow([f"2026-10-{10 + i:02d}", desc, "Food and drink", cost, "INR", f"-{float(cost) / 2:.2f}", f"{float(cost) / 2:.2f}"])
        w.writerow([])
        w.writerow(["2026-10-31", "Total balance", " ", " ", "INR", "-2545.25", "2545.25"])
    # One signed amount column, semicolon separated, BOM, month-first US dates.
    with open(out("signed-amounts-semicolon.csv"), "w", newline="", encoding="utf-8-sig") as f:
        f.write("Posting Date;Payee;Amount\r\n")
        f.write("10/13/2026;Coffee \"Beans\" Co;-4.50\r\n10/14/2026;Payroll;2500.00\r\n10/15/2026;Book store;-23.99\r\n")
    # No header at all: date, description, amount.
    with open(out("no-header.csv"), "w", newline="", encoding="utf-8") as f:
        f.write("05/10/2026,Auto rickshaw,80\n06/10/2026,Milk,62\n07/10/2026,Mobile recharge,299\n")
    # Template the app offers for download (always 100%).
    with open(out("template-filled.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Amount", "Type", "Account", "Category", "Note"])
        w.writerow(["2026-11-01", "450", "Expense", "Cash", "Food", "Lunch"])
        w.writerow(["2026-11-01", "85000", "Income", "HDFC Savings", "Salary", "November salary"])
        w.writerow(["2026-11-02", "1840", "Expense", "HDFC Savings", "Bills", "Electricity"])
    # Hostile: formula injection, script tags, emoji, Devanagari, very long note.
    with open(out("hostile.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Note", "Amount"])
        w.writerow(["01/11/2026", "=HYPERLINK(\"http://evil.example\",\"click\")", "100"])
        w.writerow(["02/11/2026", "<img src=x onerror=alert(1)>", "200"])
        w.writerow(["03/11/2026", "चाय और समोसा", "40"])
        w.writerow(["04/11/2026", "x" * 600, "50"])
        w.writerow(["05/11/2026", "+cmd|' /C calc'!A0", "60"])


# ---- 4. Messy xlsx: merged title, totals row, bad dates, hidden sheet, formulas ----
def write_messy_and_edge_xlsx():
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Notes"
    ws["A1"] = "Nothing to see here, the data is on the next sheet"
    ws2 = wb.create_sheet("October")
    ws2.merge_cells("A1:D1"); ws2["A1"] = "My October spending"
    ws2.append([])
    ws2.append(["Date", "Item", "Paid via", "Amount (Rs)"])
    data = [("01-Oct-2026", "Rent", "Bank", 18000), ("03 Oct 2026", "Electricity", "UPI", "₹1,840.00"),
            ("31/02/2026", "Impossible date", "Cash", 10), ("", "", "", ""), ("2026-10-09", "Movie", "Card", "(450)"),
            (46300, "Excel serial date", "Card", 99.999)]
    for d in data:
        ws2.append(list(d))
    r = ws2.max_row + 1
    ws2.cell(r, 2, "TOTAL"); ws2.cell(r, 4, f"=SUM(D4:D{r - 1})")
    wb.save(out("messy.xlsx"))

    wb = Workbook(); wb.save(out("empty.xlsx"))
    with open(out("corrupt.xlsx"), "wb") as f:
        f.write(b"PK\x03\x04 this is not really a zip file")
    with open(out("not-a-spreadsheet.xlsx"), "wb") as f:
        f.write(b"MZ\x90\x00 pretend executable")

    # 5,000 rows for the speed test.
    wb = Workbook(); ws = wb.active
    ws.append(["Date", "Description", "Amount"])
    d0 = datetime(2026, 1, 1)
    for i in range(5000):
        ws.append([d0 + timedelta(days=i % 300), f"Item {i}", round(10 + (i * 37) % 5000 + 0.5, 2)])
    wb.save(out("big-5000.xlsx"))


# ---- 5. Text PDF statement (hand-built, two pages, header repeated) ----
def pdf_escape(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages):
    """pages: list of [(x, y, text)]. Returns bytes of a valid PDF with Helvetica text."""
    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + len(pages) * 2
    for i, items in enumerate(pages):
        content = "BT /F1 9 Tf " + " ".join(f"1 0 0 1 {x} {y} Tm ({pdf_escape(t)}) Tj" for x, y, t in items) + " ET"
        cid = 4 + i * 2
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {cid} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        data = content.encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    buf = io.BytesIO(); buf.write(b"%PDF-1.4\n")
    offs = []
    for n, o in enumerate(objs, 1):
        offs.append(buf.tell()); buf.write(b"%d 0 obj\n" % n + o + b"\nendobj\n")
    x = buf.tell()
    buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for o in offs:
        buf.write(b"%010d 00000 n \n" % o)
    buf.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x))
    return buf.getvalue()


def write_pdfs():
    rows = [("01/10/2026", "UPI-SWIGGY ORDER 8812", "450.00", ""), ("02/10/2026", "NEFT CR SALARY ACME", "", "85,000.00"),
            ("03/10/2026", "POS AMAZON RETAIL", "1,299.00", ""), ("05/10/2026", "ATM WDL MG ROAD", "5,000.00", ""),
            ("07/10/2026", "UPI-ZOMATO 1123", "299.00", ""), ("09/10/2026", "ACH D LIC PREMIUM", "2,150.00", ""),
            ("12/10/2026", "UPI-RAVI KUMAR REFUND", "", "1,200.00"), ("15/10/2026", "BIL ELECTRICITY BESCOM", "1,840.00", ""),
            ("18/10/2026", "UPI-UBER TRIP", "186.00", ""), ("21/10/2026", "POS DMART", "2,340.50", "")]
    cols = [40, 110, 380, 470]
    pages = []
    for p in range(2):
        items = [(40, 800, "SAMPLE BANK - ACCOUNT STATEMENT"), (40, 785, f"Page {p + 1} of 2"),
                 (cols[0], 760, "Date"), (cols[1], 760, "Narration"), (cols[2], 760, "Withdrawal"), (cols[3], 760, "Deposit")]
        y = 740
        for r in rows[p * 5:(p + 1) * 5]:
            for x, t in zip(cols, r):
                if t:
                    items.append((x, y, t))
            y -= 18
        pages.append(items)
    with open(out("statement.pdf"), "wb") as f:
        f.write(build_pdf(pages))
    # Scanned: an image only, no text layer (tests the Claude path).
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 500), "white"); dr = ImageDraw.Draw(img)
    dr.text((30, 20), "SCANNED STATEMENT - no text layer", fill="black")
    for i, r in enumerate(rows[:6]):
        dr.text((30, 70 + i * 30), "   ".join(x for x in r if x), fill="black")
    img.save(out("scanned-statement.pdf"), "PDF", resolution=100)


# ---- 6. Word table ----
def write_docx():
    def cell(t):
        return f'<w:tc><w:p><w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p></w:tc>'
    rows = [["Date", "Bill", "Amount", "Paid from"], ["01/11/2026", "Internet - Airtel", "999", "HDFC Card"],
            ["03/11/2026", "Water bill", "350", "Cash"], ["05/11/2026", "Maid salary", "4,000", "UPI"],
            ["07/11/2026", "Gas cylinder", "₹ 903.50", "Cash"]]
    table = "<w:tbl>" + "".join("<w:tr>" + "".join(cell(c) for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"
    body = ('<w:p><w:r><w:t>November household bills</w:t></w:r></w:p>' + table +
            '<w:p><w:r><w:t>Paid by me, to be shared.</w:t></w:r></w:p>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}</w:body></w:document>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    with zipfile.ZipFile(out("bills.docx"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct); z.writestr("_rels/.rels", rels); z.writestr("word/document.xml", doc)
    # A Word file with no table at all.
    with zipfile.ZipFile(out("no-table.docx"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct); z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc.replace(table, "<w:p><w:r><w:t>Just a letter, no table.</w:t></w:r></w:p>"))


def main():
    os.makedirs(OUT, exist_ok=True)
    write_money_manager(); write_bank_xls(); write_csvs(); write_messy_and_edge_xlsx(); write_pdfs(); write_docx()
    for n in sorted(os.listdir(OUT)):
        print(f"  {n:34s} {os.path.getsize(out(n)):>8,} bytes")


if __name__ == "__main__":
    sys.exit(main())
