// Bulk import (Sprint 15): mapper + pure reader tests. Run: node tests/importmap_test.mjs
// Covers amounts, dates, header detection, column roles, de-dupe, warnings,
// CSV parsing, file sniffing and PDF row rebuilding, plus the sample CSV files.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseAmount, parseDate, detectDateOrder, excelSerialToISO, findHeaderRow, mapGrid, headerFingerprint }
  from "../static/js/importmap.js";
import { parseCsv, sniffDelimiter, sniffKind, decodeText, pdfItemsToRows } from "../static/js/fileimport.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.join(HERE, "fixtures", "import");
let pass = 0, fail = 0;
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function check(cond, msg) { if (cond) pass++; else { fail++; console.log("  [FAIL]", msg); } }
function same(actual, expected, msg) { check(eq(actual, expected), `${msg}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`); }
const csv = (name) => parseCsv(decodeText(new Uint8Array(fs.readFileSync(path.join(FIX, name)))));

// ---- amounts ----------------------------------------------------------------
const A = (s) => parseAmount(s)?.value ?? null;
same(A("1,23,456.78"), 123456.78, "Indian grouping");
same(A("1,234.50"), 1234.5, "western grouping");
same(A("1.234,50"), 1234.5, "European decimal comma");
same(A("12,50"), 12.5, "short decimal comma");
same(A("Rs. 450"), 450, "Rs. prefix");
same(A("₹ 1,840.00"), 1840, "rupee sign");
same(A("INR 99"), 99, "INR prefix");
same(A("(123.00)"), -123, "brackets are negative");
same(A("-123"), -123, "minus sign");
same(A("123-"), -123, "trailing minus");
same(A("+50"), 50, "plus sign");
same(parseAmount("250.00 Dr")?.marker, "dr", "Dr suffix");
same(parseAmount("CR 1,200")?.marker, "cr", "Cr prefix");
same(A("2300.0"), 2300, "text-stored number");
same(A(2300), 2300, "real number");
same(A(""), null, "blank");
same(A("abc"), null, "words");
same(A("12/10/2026"), null, "a date is not an amount");
same(A("REF00012"), null, "reference is not an amount");
same(A(NaN), null, "NaN");
same(A(Infinity), null, "Infinity");

// ---- dates ------------------------------------------------------------------
same(parseDate("30/10/2026"), "2026-10-30", "dd/mm/yyyy");
same(parseDate("30/10/2026 20:02:05"), "2026-10-30", "with time");
same(parseDate("05/10/2026", "mdy"), "2026-05-10", "mm/dd when told");
same(parseDate("2026-10-05"), "2026-10-05", "ISO");
same(parseDate("2026-10-05T10:00:00"), "2026-10-05", "ISO with T");
same(parseDate("5-Oct-26"), "2026-10-05", "5-Oct-26");
same(parseDate("03 Oct 2026"), "2026-10-03", "03 Oct 2026");
same(parseDate("Oct 5, 2026"), "2026-10-05", "Oct 5, 2026");
same(parseDate("01/10/26"), "2026-10-01", "2-digit year");
same(parseDate("31/02/2026"), null, "impossible date");
same(parseDate("29/02/2028"), "2028-02-29", "leap day");
same(parseDate("29/02/2027"), null, "not a leap year");
same(parseDate("01/01/1980"), null, "too old");
same(parseDate(46300), excelSerialToISO(46300), "Excel serial");
same(excelSerialToISO(45931), "2025-10-01", "serial 45931");
same(parseDate(new Date(2026, 9, 30, 23, 59)), "2026-10-30", "Date object late evening stays same day");
same(parseDate(""), null, "blank date");
same(parseDate("hello"), null, "words are not a date");
same(detectDateOrder(["13/10/2026", "01/10/2026"]).order, "dmy", "day-first detected");
same(detectDateOrder(["10/13/2026", "10/01/2026"]).order, "mdy", "month-first detected");
same(detectDateOrder(["03/04/2026", "05/06/2026"]), { order: "dmy", ambiguous: true }, "all ambiguous: day-first, flagged");
same(detectDateOrder(["13/10/2026", "10/13/2026"]).order, "mixed", "mixed orders flagged");

// ---- header detection ---------------------------------------------------------
const junk = [["SAMPLE BANK"], ["Statement of account"], [], ["Account No : XX1234"], ["*******"],
  ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"],
  ["01/10/26", "UPI-SWIGGY", "450.00", null, "53550.00"], ["02/10/26", "SALARY", null, "85000.00", "138550.00"]];
same(findHeaderRow(junk), 5, "header found below junk rows");

// ---- the Money Manager shape (same 12 columns as Boss's export) -------------------
{
  const g = [["Date", "Account", "Category", "Subcategory", "Note", "INR", "Income/Expense", "Tag", "Description", "Amount", "Currency", "Account"],
    [new Date(2026, 9, 30, 20, 2), "KOTAK Credit Card", "Food", "Eating out", "Dinner", 2300, "Expense", null, null, "2300.0", "INR", 2300],
    [new Date(2026, 9, 20, 15, 24), "HDFC Card", "Food", "Beverages", "Tea", 20, "Expense", null, null, "20.0", "INR", 20],
    [new Date(2026, 9, 20, 15, 24), "HDFC Card", "Food", "Beverages", "Tea", 20, "Expense", null, null, "20.0", "INR", 20],
    [new Date(2026, 9, 3, 10, 19), "Cash", "Household", "Kitchen", "Jowar atta", 30, "Expense", null, null, "30.0", "INR", 0.4],
    [new Date(2026, 9, 2), "HDFC Card", "Ajjima", null, "Vi bill - Ajjima", 1002, "Expense", null, null, "1002.0", "INR", 1002],
    [new Date(2026, 9, 1), "HDFC Card", "Salary", null, "October pay", 90000, "Income", null, null, "90000.0", "INR", 90000]];
  const r = mapGrid(g);
  check(r.ok, "money manager maps");
  const role = (h, i) => r.columns.find((c) => c.header === h && (i == null || c.index === i))?.role;
  same(role("Date"), "date", "Date column");
  same(role("Account", 1), "account", "first Account is the account");
  same(role("Account", 11), "altamount", "second Account (numbers) is a second amount, not an account");
  same(role("INR"), "amount", "INR is the amount");
  same(role("Amount"), "altamount", "text Amount column is the second amount");
  same(role("Income/Expense"), "type", "Income/Expense is the type");
  same(r.rows.length, 6, "all rows kept");
  same(r.rows.map((x) => x.direction), ["expense", "expense", "expense", "expense", "expense", "income"], "directions");
  same([r.rows[1].occurrence, r.rows[2].occurrence], [1, 2], "two identical Tea rows both kept with counters");
  check(r.rows[1].dedupe !== r.rows[2].dedupe, "identical rows get different de-dupe keys");
  check(r.warnings.some((w) => /second amount column/.test(w)), "warns that the Cash row's second amount disagrees");
  same(r.rows[4].category, "Ajjima", "custom category passed through");
  same(r.rows[0].subcategory, "Eating out", "subcategory passed through");
  same(r.rows.reduce((s, x) => s + x.amount, 0), 2300 + 20 + 20 + 30 + 1002 + 90000, "money math exact");
  check(!r.needsReview, "money manager does not need manual review");
  same(headerFingerprint(g[0]), headerFingerprint(g[0].map((h) => String(h).toUpperCase())), "fingerprint ignores case");
}

// ---- debit / credit statement ---------------------------------------------------
{
  const r = mapGrid(junk);
  check(r.ok, "bank statement maps");
  same(r.rows.map((x) => [x.date, x.amount, x.direction]), [["2026-10-01", 450, "expense"], ["2026-10-02", 85000, "income"]], "debit/credit split");
  check(!r.columns.some((c) => c.role === "amount" && /balance/i.test(c.header)), "balance never used as amount");
}

// ---- signed amounts, Dr/Cr markers, transfers, totals, zero rows ---------------------
{
  const g = [["Date", "Description", "Amount", "Type"],
    ["01/10/2026", "Coffee", "-120", ""], ["02/10/2026", "Refund", "300", "Cr"], ["03/10/2026", "Rent", "18,000 Dr", ""],
    ["04/10/2026", "Move to savings", "5000", "Transfer"], ["05/10/2026", "Zero", "0", ""], [null, "TOTAL", "23,420", ""]];
  const r = mapGrid(g);
  same(r.rows.map((x) => [x.note, x.amount, x.direction]),
    [["Coffee", 120, "expense"], ["Refund", 300, "income"], ["Rent", 18000, "expense"]], "signed + markers; transfer, zero, total skipped");
  check(r.warnings.some((w) => /3 rows skipped/.test(w)), "skipped rows reported");
}

// ---- no header / header-less file ------------------------------------------------
{
  const r = mapGrid(csv("no-header.csv"));
  check(r.ok, "header-less file maps");
  same(r.rows.map((x) => [x.date, x.note, x.amount]), [["2026-10-05", "Auto rickshaw", 80], ["2026-10-06", "Milk", 62], ["2026-10-07", "Mobile recharge", 299]], "header-less rows");
  check(r.needsReview, "header-less file asks the person to check the columns");
}

// ---- errors ---------------------------------------------------------------------
same(mapGrid([]).error, "empty", "empty grid");
same(mapGrid([[null, null], [null]]).error, "empty", "grid of blanks");
same(mapGrid([["Just a letter"], ["Dear Sir"]]).error, "no_table", "no table");
same(mapGrid([["Name", "Amount"], ["Tea", "20"], ["Milk", "30"]]).error, "no_table", "no date column");
same(mapGrid([["Date", "Note"], ["01/10/2026", "Tea"], ["02/10/2026", "x"]]).error, "no_table", "no amount column");
{
  const r = mapGrid([["Date", "Amount"], ["31/02/2026", "10"], ["01/03/2026", "20"]]);
  same(r.rows.length, 1, "bad date row skipped"); check(r.warnings.some((w) => /date could not be read/.test(w)), "bad date reported");
}

// ---- manual mapping override ------------------------------------------------------
{
  const g = [["When", "What", "How much"], ["01/10/2026", "Tea", "20"]];
  const r = mapGrid(g, { mapping: { 0: "date", 1: "note", 2: "amount" } });
  same(r.rows[0], { ...r.rows[0], date: "2026-10-01", note: "Tea", amount: 20 }, "manual mapping used");
  same(mapGrid([["Date", "Amount"], ["03/04/2026", "5"]], { dateOrder: "mdy" }).rows[0].date, "2026-03-04", "date order override");
}

// ---- text safety --------------------------------------------------------------------
{
  const r = mapGrid(csv("hostile.csv"));
  same(r.rows.length, 5, "hostile rows all read");
  same(r.rows[0].note, '=HYPERLINK("http://evil.example","click")', "formula kept as plain text (escaped on display)");
  same(r.rows[2].note, "चाय और समोसा", "Devanagari kept");
  same(r.rows[3].note.length, 200, "long note cut to 200");
}

// ---- the sample CSV files ------------------------------------------------------------
{
  const k = mapGrid(csv("kotak-style.csv"));
  check(k.ok && k.rows.length === 12, `kotak-style: 12 rows (got ${k.rows.length})`);
  check(k.rows.every((x) => x.amount > 0 && /^2026-10-/.test(x.date)), "kotak-style rows sane");
  check(k.rows.some((x) => x.amount === 100000 && x.direction === "income"), "kotak-style 1,00,000.00 credit read");
  const s = mapGrid(csv("splitwise.csv"));
  same(s.rows.map((x) => x.amount), [1800, 950, 2340.5], "splitwise: Cost column, total row skipped");
  const sc = csv("signed-amounts-semicolon.csv");
  same(sc[0], ["Posting Date", "Payee", "Amount"], "semicolon CSV with BOM split right");
  const scr = mapGrid(sc);
  same(scr.rows.map((x) => [x.date, x.note, x.direction]), [["2026-10-13", 'Coffee "Beans" Co', "expense"], ["2026-10-14", "Payroll", "expense"], ["2026-10-15", "Book store", "expense"]],
    "month-first dates detected, quotes kept");
  const scInc = mapGrid(sc, { positiveIsIncome: true });
  same(scInc.rows[1].direction, "income", "positive is income when asked");
  const t = mapGrid(csv("template-filled.csv"));
  same(t.rows.map((x) => [x.direction, x.account, x.category]), [["expense", "Cash", "Food"], ["income", "HDFC Savings", "Salary"], ["expense", "HDFC Savings", "Bills"]], "template reads at 100%");
  check(!t.needsReview, "template does not need manual review");
}

// ---- CSV parser edge cases -------------------------------------------------------------
same(parseCsv('a,"b,c",d\n1,"x ""q"" y",3'), [["a", "b,c", "d"], ["1", 'x "q" y', "3"]], "quoted commas and quotes");
same(parseCsv('a,b\r\n"line\nbreak",2\r\n'), [["a", "b"], ["line\nbreak", "2"]], "quoted newline + CRLF");
same(sniffDelimiter("a\tb\tc\n1\t2\t3"), "\t", "tab sniffed");
same(sniffDelimiter("a;b;c\n1;2,5;3"), ";", "semicolon sniffed");
same(parseCsv("﻿x,y\n1,2")[0], ["x", "y"], "BOM stripped");
same(decodeText(new Uint8Array([0x63, 0x61, 0x66, 0xe9])), "café", "Windows-1252 fallback");

// ---- sniffing -------------------------------------------------------------------------
const B = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0)));
same(sniffKind("a.pdf", B("%PDF-1.4")), "pdf", "pdf magic");
same(sniffKind("a.xls", new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])), "xls", "old xls magic");
same(sniffKind("a.xlsx", B("PK\u0003\u0004")), "xlsx", "zip as xlsx");
same(sniffKind("a.docx", B("PK\u0003\u0004")), "docx", "zip as docx");
same(sniffKind("a.xls", B("<html><table><tr><td>1</td></tr></table>")), "html", "html posing as xls");
same(sniffKind("a.exe.xlsx", B("MZ\u0090\u0000")), "binary", "executable refused");
same(sniffKind("a.xlsx", B("hello, this is text")), "broken", "text named .xlsx is broken");
same(sniffKind("a.csv", B("Date,Amount\n1/1/2026,5")), "csv", "plain csv");

// ---- PDF row rebuild (positions from pdf.js) ----------------------------------------
{
  const L = (y, cells) => cells.map(([x, str]) => ({ x, y, str, w: str.length * 4.5 }));
  const pages = [
    [...L(800, [[40, "SAMPLE BANK"]]), ...L(760, [[40, "Date"], [110, "Narration"], [380, "Withdrawal"], [470, "Deposit"]]),
     ...L(740, [[40, "01/10/2026"], [110, "UPI-SWIGGY ORDER"], [380, "450.00"]]),
     ...L(728, [[110, "REF 8812 BLR"]]),
     ...L(722 - 10, [[40, "02/10/2026"], [110, "SALARY"], [470, "85,000.00"]])],
    [...L(760, [[40, "Date"], [110, "Narration"], [380, "Withdrawal"], [470, "Deposit"]]),
     ...L(740, [[40, "03/10/2026"], [110, "POS AMAZON"], [385, "1,299.00"]])],
  ];
  const rows = pdfItemsToRows(pages);
  const r = mapGrid(rows);
  same(r.rows.map((x) => [x.date, x.amount, x.direction]), [["2026-10-01", 450, "expense"], ["2026-10-02", 85000, "income"], ["2026-10-03", 1299, "expense"]],
    "pdf columns kept apart; deposit not shifted into withdrawal");
  same(r.rows[0].note, "UPI-SWIGGY ORDER REF 8812 BLR", "wrapped narration joined");
  same(rows.filter((x) => x[0] === "Date").length, 1, "repeated page header dropped");
}

// ---- speed: 5,000 rows ---------------------------------------------------------------
{
  const g = [["Date", "Description", "Amount"]];
  for (let i = 0; i < 5000; i++) g.push([`${String((i % 28) + 1).padStart(2, "0")}/10/2026`, `Item ${i}`, String(10 + i)]);
  const t0 = Date.now(); const r = mapGrid(g); const ms = Date.now() - t0;
  check(r.rows.length === 5000 && ms < 1500, `5,000 rows mapped in ${ms} ms`);
}

console.log(`importmap: ${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
