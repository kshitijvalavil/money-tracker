"""Read an old binary Excel file (.xls, Excel 97-2003) into plain rows.

Modern files (.xlsx, .csv, Word, PDF) are read in the browser. The old binary
.xls format has no safe, maintained browser reader, so the browser sends that one
file here, after the person agrees. The bytes are parsed in memory with xlrd and
thrown away; nothing is stored or logged. Output is the same shape the browser
readers produce: {"sheets": [{"name": str, "rows": [[cell, ...], ...]}]}.
"""
import datetime

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # OLE2 compound file (BIFF .xls)
MAX_BYTES = 12 * 1024 * 1024
MAX_SHEETS = 20
MAX_ROWS = 10000
MAX_COLS = 60
MAX_CELL = 500


class XlsReadError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _cell(book, c):
    import xlrd
    t = c.ctype
    if t in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
        return None
    if t == xlrd.XL_CELL_DATE:
        try:
            d = xlrd.xldate_as_datetime(c.value, book.datemode)
        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
            return None
        return d.strftime("%Y-%m-%d %H:%M:%S") if (d.hour or d.minute or d.second) else d.strftime("%Y-%m-%d")
    if t == xlrd.XL_CELL_NUMBER:
        v = float(c.value)
        return int(v) if v.is_integer() and abs(v) < 1e15 else v
    if t == xlrd.XL_CELL_BOOLEAN:
        return bool(c.value)
    s = str(c.value)
    return s[:MAX_CELL] if s.strip() else None


def read_xls(data):
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise XlsReadError(400, "That file is empty.")
    if len(data) > MAX_BYTES:
        raise XlsReadError(413, "That file is too big. The limit is 12 MB.")
    if not data.startswith(MAGIC):
        raise XlsReadError(415, "That isn't an old-style Excel (.xls) file.")
    try:
        import xlrd
        book = xlrd.open_workbook(file_contents=bytes(data), on_demand=True, formatting_info=False,
                                  logfile=_NullLog(), ignore_workbook_corruption=False)
    except Exception:
        raise XlsReadError(422, "Couldn't open that Excel file. It may be damaged or password protected. "
                                "Try opening it and saving it as .xlsx or CSV.")
    sheets = []
    try:
        for i in range(min(book.nsheets, MAX_SHEETS)):
            sh = book.sheet_by_index(i)
            if getattr(sh, "visibility", 0):        # hidden sheet
                continue
            rows = []
            for r in range(min(sh.nrows, MAX_ROWS + 60)):
                rows.append([_cell(book, sh.cell(r, c)) for c in range(min(sh.ncols, MAX_COLS))])
            sheets.append({"name": str(sh.name)[:60], "rows": rows})
            book.unload_sheet(i)
    except Exception:
        raise XlsReadError(422, "Couldn't read that Excel file. Try saving it as .xlsx or CSV.")
    finally:
        book.release_resources()
    if not sheets:
        raise XlsReadError(422, "That Excel file has no visible sheets.")
    return {"sheets": sheets}


class _NullLog:
    """xlrd writes warnings to a log file; keep file contents out of server logs."""
    def write(self, *_):
        pass

    def flush(self):
        pass


_ = datetime  # (kept for readers of this module: dates come back as ISO strings)
