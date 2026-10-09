"""
SQL over attached CSV, TSV and Excel files, using DuckDB.

At upload, each file (each sheet, for Excel) is loaded into a small DuckDB database stored next
to the upload (tables.duckdb), with column types detected from the data and column names
normalised to snake_case. At query time, the chat's files are attached read-only to a fresh
in-memory DuckDB, exposed as views with chat-level table names, and the session is locked down:

  1. Exactly one statement, and it must be a SELECT (or EXPLAIN of one).
  2. enable_external_access=false + lock_configuration=true: no reading or writing files, no
     ATTACH, no extensions, and the settings can't be changed back.
  3. The data files are attached READ_ONLY, and the database itself is a throwaway in-memory one.
  4. A timeout (interrupt) and a row cap, like PostgreSQL queries.

DuckDB's SQL is close to PostgreSQL's (::casts, ILIKE, date_trunc, string_agg, window functions).
"""
import csv
import datetime
import os
import re
import threading
import time

try:
    import duckdb
except ImportError:  # optional dependency
    duckdb = None

TABLE_EXTS = {".csv", ".tsv", ".xlsx"}
HEAD_BYTES = 64 * 1024      # how much of each file the text preview reads
DB_FILE = "tables.duckdb"
SOURCE = "files"            # what the model writes in "-- db: files"
SAMPLE_ROWS = 3
PREVIEW_LINES = 15
MEMORY_LIMIT = os.getenv("FILES_SQL_MEMORY", "1GB")
# "calamine" (default, ~10x faster, ~1 GB RAM per million-row sheet) or "openpyxl" (slow, low memory)
EXCEL_READER = os.getenv("EXCEL_READER", "calamine").strip().lower()


def available():
    return duckdb is not None


def is_tabular(name):
    return os.path.splitext(name)[1].lower() in TABLE_EXTS


def _ident(name):
    n = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "table"
    if n[0].isdigit():
        n = "t_" + n
    return n[:48]


def _q(name):
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------- upload time
def _utf8_copy(path, folder, idx):
    """Copy a CSV to UTF-8 (Excel often saves Windows-1252)."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    out = os.path.join(folder, f"source_{idx}_utf8.csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return out


def _xcell(v):
    """An Excel cell value as CSV text that DuckDB will type correctly."""
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, datetime.timedelta):
        return str(v)
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)  # Excel stores every number as a float; keep whole numbers as integers
    return v


def _excel_rows(path):
    """Yield (sheet_name, row iterator) per sheet: python-calamine if installed (~10x faster), else openpyxl."""
    python_calamine = None
    if EXCEL_READER != "openpyxl":
        try:
            import python_calamine
        except ImportError:
            pass
    if python_calamine:
        try:
            wb = python_calamine.CalamineWorkbook.from_path(path)
            names = wb.sheet_names
        except BaseException as e:  # calamine's Rust panics arrive as pyo3 PanicException, a BaseException
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            python_calamine = None   # fall back to openpyxl below
        else:
            for name in names:
                try:
                    sheet = wb.get_sheet_by_name(name)
                    if not sheet.height:   # calamine panics on iter_rows() for a sheet with no cells
                        continue
                    rows = sheet.iter_rows()
                except BaseException as e:  # any other reader panic: skip the sheet
                    if isinstance(e, (KeyboardInterrupt, SystemExit)):
                        raise
                    continue
                yield name, rows
            return
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            yield ws.title, ws.iter_rows(values_only=True)
    finally:
        wb.close()


def _xlsx_sheets(path, folder):
    """Write each non-empty sheet to a CSV; returns [(sheet_name, csv_path)]."""
    out = []
    for i, (title, rows) in enumerate(_excel_rows(path)):
        dest = os.path.join(folder, f"sheet_{i}.csv")
        n = 0
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            for row in rows:
                if not any(v is not None and str(v).strip() != "" for v in row):
                    continue
                w.writerow([_xcell(v) for v in row])
                n += 1
        if n:
            out.append((title, dest))
        else:
            os.remove(dest)
    return out


def prepare(path, name, folder):
    """Load a tabular upload into folder/tables.duckdb.

    Returns [{"table", "sheet", "columns": [[name, type]], "rows"}], or [] if nothing loadable.
    """
    ext = os.path.splitext(name)[1].lower()
    if ext == ".xlsx":
        try:
            sources = [(sheet, p, None) for sheet, p in _xlsx_sheets(path, folder)]
        except BaseException as e:  # don't let a reader panic take down the request
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            raise ValueError(f"Couldn't read this Excel file: {e}") from None
    else:
        sources = [("", path, "\t" if ext == ".tsv" else None)]
    dbpath = os.path.join(folder, DB_FILE)
    con = duckdb.connect(dbpath)
    out = []
    try:
        for i, (sheet, src, delim) in enumerate(sources):
            table = f"t{i}"
            opts = "normalize_names=true, sample_size=-1, null_padding=true" + (", delim='\\t'" if delim else "")
            sql = f"CREATE TABLE {table} AS SELECT * FROM read_csv(?, {opts})"
            try:
                con.execute(sql, [src])
            except duckdb.Error:
                con.execute(f"DROP TABLE IF EXISTS {table}")
                con.execute(sql, [_utf8_copy(src, folder, i)])  # retry as UTF-8
            cols = [[c[0], c[1]] for c in con.execute(f"DESCRIBE {table}").fetchall()]
            rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if cols:
                out.append({"table": table, "sheet": sheet, "columns": cols, "rows": int(rows),
                            "src": os.path.basename(src)})
    finally:
        con.close()
    if not out and os.path.exists(dbpath):
        os.remove(dbpath)
    return out


# ---------------------------------------------------------------- chat level
def chat_tables(file_ids, load_meta, upload_dir):
    """Tables for the chat's files, in order, with unique chat-level names.

    Returns [{"name", "fid", "file", "sheet", "table", "columns", "rows", "db"}].
    The same ordered ids always give the same names, so the prompt and the queries agree.
    """
    out, used = [], set()
    for fid in file_ids:
        meta = load_meta(fid)
        if not meta or not meta.get("tables"):
            continue
        db = os.path.join(upload_dir, fid, DB_FILE)
        if not os.path.exists(db):
            continue
        base = _ident(os.path.splitext(meta["name"])[0])
        for t in meta["tables"]:
            name = base if not t.get("sheet") or len(meta["tables"]) == 1 else _ident(f"{base}_{t['sheet']}")
            unique, n = name, 2
            while unique in used:
                unique, n = f"{name}_{n}", n + 1
            used.add(unique)
            out.append(dict(t, name=unique, fid=fid, file=meta["name"], db=db))
    return out


def _connect(tables):
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
    con.execute("SET threads=2")
    aliases = {}
    for t in tables:
        if t["db"] not in aliases:
            alias = f"f{len(aliases)}"
            con.execute(f"ATTACH '{t['db'].replace(chr(39), chr(39) * 2)}' AS {alias} (READ_ONLY)")
            aliases[t["db"]] = alias
        con.execute(f"CREATE VIEW {_q(t['name'])} AS SELECT * FROM {aliases[t['db']]}.{t['table']}")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return con


def _cell(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v[:2000] if isinstance(v, str) else v
    if isinstance(v, float):
        return v if v == v and abs(v) != float("inf") else str(v)
    return str(v)[:2000]


def run_query(sql, tables, max_rows, timeout_ms):
    """Run one read-only SELECT over the chat's file tables."""
    if not tables:
        raise ValueError("This chat has no attached CSV or Excel files to query.")
    t0 = time.time()
    con = _connect(tables)
    timer = threading.Timer(timeout_ms / 1000, con.interrupt)
    try:
        stmts = con.extract_statements(sql)
        if len(stmts) != 1:
            raise ValueError("Run one statement at a time.")
        if stmts[0].type not in (duckdb.StatementType.SELECT, duckdb.StatementType.EXPLAIN):
            raise ValueError("Only read-only queries can be run on attached files (SELECT, WITH, EXPLAIN).")
        timer.start()
        try:
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1) if columns else []
        except duckdb.InterruptException:
            raise ValueError(f"The query took longer than {timeout_ms // 1000}s and was stopped.")
    finally:
        timer.cancel()
        con.close()
    truncated = len(rows) > max_rows
    return {"db_id": SOURCE, "db_name": SOURCE, "columns": columns,
            "rows": [[_cell(v) for v in r] for r in rows[:max_rows]], "row_count": min(len(rows), max_rows),
            "truncated": truncated, "ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- prompt
def _md(v):
    s = "NULL" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")[:60]


def prompt_section(tables, max_chars=20000):
    """Schema of the chat's file tables, with a few sample rows each."""
    lines, samples = [], {}
    try:
        con = _connect(tables)
        try:
            for t in tables:
                samples[t["name"]] = con.execute(f"SELECT * FROM {_q(t['name'])} LIMIT {SAMPLE_ROWS}").fetchall()
        finally:
            con.close()
    except Exception:
        pass
    for t in tables:
        src = t["file"] + (f", sheet '{t['sheet']}'" if t.get("sheet") else "")
        block = [f"{t['name']}  (from {src}; {t['rows']:,} rows)"]
        block += [f"  {c} {typ}" for c, typ in t["columns"]]
        rows = samples.get(t["name"])
        if rows:
            block.append("  sample rows:")
            block.append("  | " + " | ".join(c for c, _ in t["columns"]) + " |")
            block += ["  | " + " | ".join(_md(v) for v in r) + " |" for r in rows]
        text = "\n".join(block)
        if sum(len(x) for x in lines) + len(text) > max_chars:
            lines.append(f"... {len(tables) - tables.index(t)} more table(s) not shown")
            break
        lines.append(text)
    return ("Files attached to this chat, loaded as tables you can query with SQL. This source uses DuckDB, "
            "whose SQL is close to PostgreSQL (::casts, ILIKE, date_trunc, string_agg and window functions work). "
            "Column names were normalised to lower_snake_case. Prefer SQL over reading the file text for counts, "
            "totals, averages, rankings and filters; the file text you see is only a preview.\n\n"
            + "\n\n".join(lines))


def _read_head(path, max_lines):
    """First lines of a text file, without reading the whole (possibly huge) file."""
    with open(path, "rb") as fh:
        raw = fh.read(HEAD_BYTES)
    cut = len(raw) == HEAD_BYTES
    text = None
    for trim in (0, 1, 2, 3) if cut else (0,):  # a cut can split a UTF-8 character
        try:
            text = raw[:len(raw) - trim].decode("utf-8-sig")
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    lines = text.splitlines()
    if cut:
        lines = lines[:-1]  # the last line may be incomplete
    return "\n".join(lines[:max_lines])


def head_text(folder, tables, max_lines=200):
    """Text kept for a tabular upload: the first lines of each table's source, not the whole file."""
    parts = []
    for t in tables:
        head = _read_head(os.path.join(folder, t["src"]), max_lines)
        parts.append(f"[Sheet: {t['sheet']}]\n{head}" if t.get("sheet") else head)
    return "\n".join(parts)


def cleanup(folder):
    """Delete intermediate CSVs (Excel sheets, UTF-8 copies) once the tables and preview exist."""
    for f in os.listdir(folder):
        if (f.startswith("sheet_") or f.startswith("source_")) and f.endswith(".csv"):
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass


def preview(text, tables):
    """Short preview of a tabular file for the message body, instead of the full text."""
    head = "\n".join(text.splitlines()[:PREVIEW_LINES])
    names = ", ".join(f"{t['name']} ({t['rows']:,} rows)" for t in tables)
    return f"[Loaded as SQL table(s): {names}. Only the first {PREVIEW_LINES} lines are shown here; query the table for the rest.]\n{head}"
