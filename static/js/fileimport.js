// File readers for bulk import (Sprint 15). Every reader turns a file into the
// same thing: { kind, sheets: [{ name, rows: [[cell, ...], ...] }], textChars }
// which importmap.js then maps. Everything here runs in the browser; the file
// never leaves the device. Two exceptions, both only after the person says yes:
// an old binary .xls goes to /api/read-xls, and a scanned PDF's page images go
// to /api/read-statement (Claude).

export const MAX_BYTES = 12 * 1024 * 1024;          // same cap as the server
const MAX_UNZIPPED = 80 * 1024 * 1024;              // zip-bomb guard (all entries)
const MAX_ENTRY = 40 * 1024 * 1024;                 // per entry
const MAX_ENTRIES = 3000;
const MAX_ROWS = 10060;                             // 10,000 rows + some header junk
const MAX_PDF_PAGES = 60;

export class ImportError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}

// ---- detect kind ---------------------------------------------------------------
export function sniffKind(name, bytes) {
  const ext = String(name || "").toLowerCase().split(".").pop();
  const b = bytes;
  const starts = (...sig) => sig.every((v, i) => b[i] === v);
  if (b.length >= 4 && starts(0x25, 0x50, 0x44, 0x46)) return "pdf";            // %PDF
  if (b.length >= 8 && starts(0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1)) return "xls";   // OLE2
  if (b.length >= 4 && starts(0x50, 0x4b, 0x03, 0x04)) {                          // zip
    if (ext === "docx") return "docx";
    if (ext === "xlsx" || ext === "xlsm") return "xlsx";
    return "zip";                                                                  // decide after listing
  }
  if (b.length >= 2 && (starts(0x4d, 0x5a) || starts(0x7f, 0x45))) return "binary"; // MZ / ELF
  // Text: CSV/TSV, or an "xls" that is really an HTML table (common bank export).
  const head = new TextDecoder("utf-8", { fatal: false }).decode(b.slice(0, 2048)).replace(/^﻿/, "").trimStart().toLowerCase();
  if (head.startsWith("<") && /<table|<html|<!doctype/.test(head)) return "html";
  if (["xlsx", "docx", "pdf"].includes(ext)) return "broken";
  if (/[\u0000-\u0008\u000e-\u001f]/.test(head.replace(/[\r\n\t]/g, ""))) return "binary";
  return "csv";
}

// ---- tiny zip reader (stored + deflate), using the browser's own decompressor ----
async function inflateRaw(data, limit) {
  if (typeof DecompressionStream === "undefined") {
    throw new ImportError("unsupported", "This browser can't open Excel or Word files. Update it, or save the file as CSV.");
  }
  const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  const reader = stream.getReader();
  const parts = []; let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (total > limit) { reader.cancel(); throw new ImportError("too_big", "That file unpacks to something far too big to be a spreadsheet."); }
    parts.push(value);
  }
  const out = new Uint8Array(total); let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export async function unzip(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 65557); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new ImportError("corrupt", "That file is damaged or isn't really an Excel or Word file.");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  if (count > MAX_ENTRIES || p >= bytes.length) throw new ImportError("corrupt", "That file is damaged.");
  const entries = new Map(); let unzipped = 0;
  const dec = new TextDecoder();
  for (let n = 0; n < count; n++) {
    if (p + 46 > bytes.length || dv.getUint32(p, true) !== 0x02014b50) throw new ImportError("corrupt", "That file is damaged.");
    const flags = dv.getUint16(p + 8, true);
    const method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
    const nlen = dv.getUint16(p + 28, true), xlen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
    const local = dv.getUint32(p + 42, true);
    const name = dec.decode(bytes.subarray(p + 46, p + 46 + nlen));
    p += 46 + nlen + xlen + clen;
    if (flags & 1) throw new ImportError("encrypted", "That file is password protected. Remove the password and try again.");
    if (csize === 0xffffffff || usize === 0xffffffff) throw new ImportError("too_big", "That file is too big to import.");
    entries.set(name, { method, csize, usize, local });
    unzipped += usize;
    if (unzipped > MAX_UNZIPPED) throw new ImportError("too_big", "That file unpacks to something far too big to be a spreadsheet.");
  }
  return {
    names: () => [...entries.keys()],
    has: (n) => entries.has(n),
    async text(n) {
      const e = entries.get(n);
      if (!e) return null;
      if (e.local + 30 > bytes.length || dv.getUint32(e.local, true) !== 0x04034b50) throw new ImportError("corrupt", "That file is damaged.");
      const start = e.local + 30 + dv.getUint16(e.local + 26, true) + dv.getUint16(e.local + 28, true);
      const raw = bytes.subarray(start, start + e.csize);
      let data;
      if (e.method === 0) data = raw;
      else if (e.method === 8) data = await inflateRaw(raw, Math.min(MAX_ENTRY, Math.max(e.usize, 1) * 1.01 + 1024));
      else throw new ImportError("unsupported", "That file uses a compression this app can't open. Save it again as .xlsx or CSV.");
      return dec.decode(data);
    },
  };
}

const parseXml = (s) => {
  const doc = new DOMParser().parseFromString(s, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new ImportError("corrupt", "That file is damaged.");
  return doc;
};
const byTag = (el, tag) => [...el.getElementsByTagNameNS("*", tag)];
const kids = (el, tag) => [...el.children].filter((c) => c.localName === tag);

// ---- XLSX ------------------------------------------------------------------------
const BUILTIN_DATE_FMTS = new Set([14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 30, 36, 45, 46, 47, 50, 57]);
function isDateCode(code) {
  const c = String(code).replace(/"[^"]*"|\[[^\]]*\]|\\.|_./g, "");
  return /[dmyhs]/i.test(c) && !/^[#0.,%E+\-\s]*$/i.test(c);
}
function serialToDate(n, date1904) {
  const ms = Math.round(((date1904 ? n + 1462 : n) - 25569) * 86400000);
  const u = new Date(ms);
  return new Date(u.getUTCFullYear(), u.getUTCMonth(), u.getUTCDate(), u.getUTCHours(), u.getUTCMinutes(), u.getUTCSeconds());
}
const colIndex = (ref) => {
  const letters = String(ref).match(/^[A-Z]+/i)?.[0] || "";
  let n = 0; for (const ch of letters.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
};

export async function readXlsx(zip) {
  const wbXml = await zip.text("xl/workbook.xml");
  if (!wbXml) throw new ImportError("corrupt", "That isn't a readable Excel file.");
  const wb = parseXml(wbXml);
  const date1904 = byTag(wb, "workbookPr").some((e) => /^(1|true)$/.test(e.getAttribute("date1904") || ""));
  const rels = parseXml((await zip.text("xl/_rels/workbook.xml.rels")) || "<Relationships/>");
  const target = {};
  byTag(rels, "Relationship").forEach((r) => {
    let t = r.getAttribute("Target") || "";
    t = t.startsWith("/") ? t.slice(1) : "xl/" + t.replace(/^\.\//, "");
    target[r.getAttribute("Id")] = t;
  });
  const shared = [];
  const ssXml = await zip.text("xl/sharedStrings.xml");
  if (ssXml) byTag(parseXml(ssXml), "si").forEach((si) => shared.push(byTag(si, "t").filter((t) => t.parentNode.localName !== "rPh").map((t) => t.textContent).join("")));
  const dateStyle = [];
  const stXml = await zip.text("xl/styles.xml");
  if (stXml) {
    const st = parseXml(stXml);
    const custom = {};
    byTag(st, "numFmt").forEach((f) => { custom[+f.getAttribute("numFmtId")] = f.getAttribute("formatCode"); });
    const xfs = byTag(st, "cellXfs")[0];
    if (xfs) kids(xfs, "xf").forEach((xf, i) => {
      const id = +(xf.getAttribute("numFmtId") || 0);
      dateStyle[i] = BUILTIN_DATE_FMTS.has(id) || (custom[id] ? isDateCode(custom[id]) : false);
    });
  }
  const sheets = [];
  for (const s of byTag(wb, "sheet")) {
    if (["hidden", "veryHidden"].includes(s.getAttribute("state"))) continue;
    const rid = s.getAttributeNS("http://schemas.openxmlformats.org/officeDocument/2006/relationships", "id") || s.getAttribute("r:id");
    const path = target[rid];
    const xml = path && (await zip.text(path));
    if (!xml) continue;
    const doc = parseXml(xml);
    const rows = [];
    for (const row of byTag(doc, "row")) {
      if (row.getAttribute("hidden") === "1") continue;
      const r = [];
      for (const c of kids(row, "c")) {
        const ci = c.getAttribute("r") ? colIndex(c.getAttribute("r")) : r.length;
        if (ci > 200) continue;
        const t = c.getAttribute("t");
        const v = kids(c, "v")[0]?.textContent;
        let val = null;
        if (t === "s") val = v != null ? shared[+v] ?? null : null;
        else if (t === "inlineStr") val = byTag(c, "t").map((x) => x.textContent).join("");
        else if (t === "str") val = v ?? null;
        else if (t === "b") val = v === "1";
        else if (t === "e") val = null;
        else if (v != null && v !== "") {
          const n = Number(v);
          val = Number.isFinite(n) ? (dateStyle[+(c.getAttribute("s") || 0)] ? serialToDate(n, date1904) : n) : v;
        }
        while (r.length < ci) r.push(null);
        r[ci] = val;
      }
      const ri = row.getAttribute("r") ? +row.getAttribute("r") - 1 : rows.length;
      while (rows.length < ri && rows.length < MAX_ROWS) rows.push([]);
      if (rows.length >= MAX_ROWS) throw new ImportError("too_many", "That sheet has more than 10,000 rows. Split it into smaller files.");
      rows.push(r);
    }
    sheets.push({ name: s.getAttribute("name") || "Sheet", rows });
  }
  return sheets;
}

// ---- DOCX (tables) ---------------------------------------------------------------
export async function readDocx(zip) {
  const xml = await zip.text("word/document.xml");
  if (!xml) throw new ImportError("corrupt", "That isn't a readable Word file.");
  const doc = parseXml(xml);
  const cellText = (tc) => {
    // Only this cell's own paragraphs, not nested tables.
    const parts = [];
    for (const p of kids(tc, "p")) {
      const bits = [];
      byTag(p, "r").forEach((r) => [...r.children].forEach((x) => {
        if (x.localName === "t") bits.push(x.textContent);
        else if (x.localName === "tab" || x.localName === "br") bits.push(" ");
      }));
      parts.push(bits.join(""));
    }
    return parts.join(" ").replace(/\s+/g, " ").trim();
  };
  const sheets = [];
  byTag(doc, "tbl").forEach((tbl, i) => {
    if (tbl.parentNode && tbl.parentNode.localName === "tc") return;        // nested table: skip
    const rows = kids(tbl, "tr").map((tr) => {
      const r = [];
      for (const tc of kids(tr, "tc")) {
        const pr = kids(tc, "tcPr")[0];
        const span = +(pr && kids(pr, "gridSpan")[0]?.getAttribute("w:val") || pr && kids(pr, "gridSpan")[0]?.getAttributeNS("*", "val") || 1) || 1;
        const vm = pr && kids(pr, "vMerge")[0];
        const cont = vm && !/restart/.test(vm.getAttribute("w:val") || "");
        r.push(cont ? null : (cellText(tc) || null));
        for (let k = 1; k < Math.min(span, 20); k++) r.push(null);
      }
      return r;
    });
    if (rows.length) sheets.push({ name: `Table ${i + 1}`, rows });
  });
  if (!sheets.length) throw new ImportError("no_table", "That Word file has no table. Put the expenses in a table (Date, Item, Amount) and try again.");
  return sheets;
}

// ---- CSV / TSV -----------------------------------------------------------------
export function sniffDelimiter(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim()).slice(0, 20);
  let best = ",", bestScore = -1;
  for (const d of [",", ";", "\t", "|"]) {
    const counts = lines.map((l) => splitCsvLine(l, d).length);
    const common = counts.filter((c) => c === counts[0]).length;
    const score = counts[0] > 1 ? common * 10 + counts[0] : 0;
    if (score > bestScore) { best = d; bestScore = score; }
  }
  return best;
}
function splitCsvLine(line, d) { return parseCsv(line, d)[0] || []; }

export function parseCsv(text, delim) {
  text = String(text).replace(/^﻿/, "");
  const d = delim || sniffDelimiter(text);
  const rows = []; let row = [], field = "", q = false, i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (q) {
      if (ch === '"') { if (text[i + 1] === '"') { field += '"'; i += 2; continue; } q = false; i++; continue; }
      field += ch; i++; continue;
    }
    if (ch === '"' && field.trim() === "") { q = true; field = ""; i++; continue; }
    if (ch === d) { row.push(field); field = ""; i++; continue; }
    if (ch === "\r" || ch === "\n") {
      row.push(field); rows.push(row); row = []; field = "";
      if (ch === "\r" && text[i + 1] === "\n") i++;
      i++;
      if (rows.length > MAX_ROWS) throw new ImportError("too_many", "That file has more than 10,000 rows. Split it into smaller files.");
      continue;
    }
    field += ch; i++;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.map((r) => r.map((c) => (c.trim() === "" ? null : c.trim())));
}

export function decodeText(bytes) {
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes);
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes);
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { return new TextDecoder("windows-1252").decode(bytes); }
}

// ---- HTML table (".xls" that is really a web page) ----------------------------------
export function readHtmlTables(text) {
  const doc = new DOMParser().parseFromString(text, "text/html");     // inert: scripts don't run
  const sheets = [];
  doc.querySelectorAll("table").forEach((t, i) => {
    const rows = [...t.rows].map((tr) => [...tr.cells].flatMap((c) => {
      const v = c.textContent.replace(/\s+/g, " ").trim() || null;
      return [v, ...Array(Math.min(Math.max((+c.colSpan || 1) - 1, 0), 20)).fill(null)];
    }));
    if (rows.length) sheets.push({ name: `Table ${i + 1}`, rows });
  });
  if (!sheets.length) throw new ImportError("no_table", "Couldn't find a table in that file.");
  return sheets;
}

// ---- PDF -------------------------------------------------------------------------
let pdfjsPromise = null;
async function pdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import("/static/vendor/pdfjs/pdf.min.mjs").then((m) => {
      m.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.min.mjs";
      return m;
    });
  }
  return pdfjsPromise;
}

const HEADER_WORDS = /^(date|txn date|transaction date|value date|value dt|narration|description|particulars|details|withdrawal|withdrawals|withdrawal amt\.?|debit|deposit|deposits|deposit amt\.?|credit|amount|balance|closing balance|chq|ref|cheque)/i;

// Turn positioned text into rows. If a header line is found, cells are assigned
// to its columns (so a blank Withdrawal cell doesn't shift Deposit left);
// otherwise lines are split on wide gaps.
export function pdfItemsToRows(pages) {
  const lines = [];
  pages.forEach((items, pi) => {
    const byY = [];
    for (const it of items) {
      if (!it.str || !it.str.trim()) continue;
      let l = byY.find((x) => Math.abs(x.y - it.y) < 3);
      if (!l) { l = { y: it.y, page: pi, items: [] }; byY.push(l); }
      l.items.push(it);
    }
    byY.sort((a, b) => b.y - a.y).forEach((l) => { l.items.sort((a, b) => a.x - b.x); lines.push(l); });
  });
  const headerLine = lines.find((l) => l.items.filter((it) => HEADER_WORDS.test(it.str.trim())).length >= 2);
  const rows = [];
  if (headerLine) {
    const cols = headerLine.items.map((it) => ({ x0: it.x, x1: it.x + (it.w || it.str.length * 4), text: it.str.trim() }));
    const headerText = cols.map((c) => c.text.toLowerCase()).join("|");
    const bounds = cols.map((c, i) => [i === 0 ? -1e9 : (cols[i - 1].x1 + c.x0) / 2, i === cols.length - 1 ? 1e9 : (c.x1 + cols[i + 1].x0) / 2]);
    let started = false;
    for (const l of lines) {
      if (l === headerLine) { rows.push(cols.map((c) => c.text)); started = true; continue; }
      if (!started) { rows.push([l.items.map((i) => i.str).join(" ")]); continue; }
      const t = l.items.map((i) => i.str.trim().toLowerCase()).join("|");
      if (t === headerText) continue;                                    // header repeated on a new page
      const r = cols.map(() => null);
      for (const it of l.items) {
        const cx = it.x + (it.w || it.str.length * 4) / 2;
        let k = bounds.findIndex(([a, b]) => cx >= a && cx < b);
        if (k < 0) k = cols.length - 1;
        r[k] = r[k] ? r[k] + " " + it.str.trim() : it.str.trim();
      }
      // Wrapped narration: a line with text in one non-date column only joins the row above.
      const filled = r.filter((x) => x != null).length;
      const prev = rows[rows.length - 1];
      if (filled === 1 && prev && r[0] == null && !/\d[\d,]*\.\d{2}/.test(r.find((x) => x) || "")) {
        const k = r.findIndex((x) => x != null);
        prev[k] = prev[k] ? prev[k] + " " + r[k] : r[k];
        continue;
      }
      rows.push(r);
    }
  } else {
    for (const l of lines) {
      const cells = []; let cur = null;
      for (const it of l.items) {
        const w = it.w || it.str.length * 4;
        if (cur && it.x - cur.end < 12) { cur.text += (it.x - cur.end > 1.5 ? " " : "") + it.str; cur.end = it.x + w; }
        else { cur = { text: it.str, end: it.x + w }; cells.push(cur); }
      }
      rows.push(cells.map((c) => c.text.trim() || null));
    }
  }
  return rows;
}

export async function readPdf(bytes, { password } = {}) {
  const lib = await pdfjs();
  let doc;
  try {
    doc = await lib.getDocument({ data: bytes, isEvalSupported: false, disableFontFace: true, password,
      useSystemFonts: false, enableXfa: false, stopAtErrors: false }).promise;
  } catch (e) {
    if (e && e.name === "PasswordException") throw new ImportError("encrypted", "That PDF is password protected. Many bank statements use your customer ID or date of birth; enter it below.");
    throw new ImportError("corrupt", "Couldn't open that PDF. It may be damaged.");
  }
  if (doc.numPages > MAX_PDF_PAGES) throw new ImportError("too_many", `That PDF has more than ${MAX_PDF_PAGES} pages. Split it and try again.`);
  const pages = []; let textChars = 0;
  for (let p = 1; p <= doc.numPages; p++) {
    const page = await doc.getPage(p);
    const tc = await page.getTextContent();
    const items = tc.items.filter((i) => "str" in i).map((i) => ({ str: i.str, x: i.transform[4], y: i.transform[5], w: i.width }));
    textChars += items.reduce((s, i) => s + i.str.trim().length, 0);
    pages.push(items);
  }
  const result = { sheets: [{ name: "PDF", rows: pdfItemsToRows(pages) }], textChars, doc };
  return result;
}

// Render the first pages of a (scanned) PDF to JPEG for the Claude fallback.
export async function pdfPageImages(doc, maxPages = 5) {
  const out = [];
  for (let p = 1; p <= Math.min(doc.numPages, maxPages); p++) {
    const page = await doc.getPage(p);
    const vp0 = page.getViewport({ scale: 1 });
    const scale = Math.min(2, 1600 / Math.max(vp0.width, vp0.height));
    const vp = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(vp.width); canvas.height = Math.ceil(vp.height);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
    out.push({ mime: "image/jpeg", data: canvas.toDataURL("image/jpeg", 0.8).split(",")[1] });
  }
  return out;
}

// ---- entry point ------------------------------------------------------------------
// Returns { kind, sheets, textChars, needsServer?: 'xls', pdfDoc? }.
export async function readFile(file, opts = {}) {
  if (!file || !file.size) throw new ImportError("empty", "That file is empty.");
  if (file.size > MAX_BYTES) throw new ImportError("too_big", "That file is too big. The limit is 12 MB.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let kind = sniffKind(file.name, bytes);
  if (kind === "binary") throw new ImportError("wrong_type", "That isn't a spreadsheet, PDF or Word file.");
  if (kind === "broken") throw new ImportError("corrupt", "That file is damaged or isn't really what its name says.");
  if (kind === "xls") return { kind, sheets: [], needsServer: "xls", bytes };
  if (kind === "pdf") { const r = await readPdf(bytes, opts); return { kind, sheets: r.sheets, textChars: r.textChars, pdfDoc: r.doc }; }
  if (kind === "zip" || kind === "xlsx" || kind === "docx") {
    const zip = await unzip(bytes);
    if (zip.has("word/document.xml")) kind = "docx";
    else if (zip.has("xl/workbook.xml")) kind = "xlsx";
    else throw new ImportError("wrong_type", "That zip file isn't an Excel or Word document.");
    const sheets = kind === "docx" ? await readDocx(zip) : await readXlsx(zip);
    return { kind, sheets };
  }
  const text = decodeText(bytes);
  if (kind === "html") return { kind: "xls", sheets: readHtmlTables(text) };
  return { kind: "csv", sheets: [{ name: file.name || "CSV", rows: parseCsv(text) }] };
}

// Pick the sheet that looks most like a list of transactions.
export function bestSheet(sheets, mapGrid) {
  let best = 0, bestN = -1;
  sheets.forEach((s, i) => {
    const r = mapGrid(s.rows);
    const n = r.ok ? r.rows.length : 0;
    if (n > bestN) { best = i; bestN = n; }
  });
  return best;
}
