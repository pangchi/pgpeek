"""
Read-only PostgreSQL access for the chat app, with multiple saved connections.

Connections are stored in SQLite (store.py, secrets encrypted). DATABASE_URL in .env, if set,
appears as an extra connection with id "env" that can't be edited in the UI.

Safety layers (any one of them blocks writes; together they're belt and braces):
  1. Only statements starting with SELECT, WITH, EXPLAIN, SHOW, VALUES or TABLE are accepted.
  2. Queries run as prepared statements, so Postgres rejects more than one statement
     ("SELECT 1; DELETE ..." fails before anything runs).
  3. The session sets default_transaction_read_only and each query runs inside
     BEGIN READ ONLY, which is always rolled back.
  4. statement_timeout and a row cap stop runaway queries.
For real protection, also connect as a role that only has SELECT grants (see README).

A connection can go through an SSH tunnel (ssh_tunnel.py). Its database host/port are then
as seen from the SSH server, e.g. localhost:5432.
"""
import math
import os
import re
import threading
import time

import ssh_tunnel
import store

try:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:  # optional dependency
    psycopg = None

DB_MAX_ROWS = int(os.getenv("DB_MAX_ROWS", 200))
DB_TIMEOUT_MS = int(os.getenv("DB_TIMEOUT_MS", 15000))
DB_SCHEMAS = os.getenv("DB_SCHEMAS", "public")
DB_SCHEMA_CHARS = int(os.getenv("DB_SCHEMA_CHARS", 20000))
DB_RESULT_CHARS = int(os.getenv("DB_RESULT_CHARS", 20000))
SCHEMA_CACHE_SECS = 300

ALLOWED_FIRST_WORDS = {"select", "with", "explain", "show", "values", "table"}
DB_TAG_RE = re.compile(r"^\s*--\s*db\s*:\s*(.+?)\s*$", re.I | re.M)

_lock = threading.Lock()
_schema_cache = {}   # cid -> {"at", "fp", "text", "tables"}

ENV_ID = "env"
_env_dsn = os.getenv("DATABASE_URL", "").strip()


def available():
    return psycopg is not None


# ---------------------------------------------------------------- connection configs
def _env_config():
    if not _env_dsn:
        return None
    name = os.getenv("DATABASE_LABEL", "").strip()
    if not name and psycopg:
        name = conninfo_to_dict(_env_dsn).get("dbname") or "env"
    return {"id": ENV_ID, "name": name or "env", "dsn": _env_dsn, "ssh": ssh_tunnel.env_config(),
            "schemas": DB_SCHEMAS, "context": os.getenv("DATABASE_CONTEXT", ""), "source": "env"}


def _all_configs():
    out = [dict(c, source="saved") for c in store.list_all()]
    env = _env_config()
    if env and not any(c["name"].lower() == env["name"].lower() for c in out):
        out.insert(0, env)
    return out


def config(cid):
    if cid == ENV_ID:
        c = _env_config()
    else:
        c = store.get(cid)
        if c:
            c["source"] = "saved"
    if not c:
        raise KeyError(f"No saved database connection with id {cid}.")
    return c


def public(c):
    """A connection without secrets, for the browser."""
    out = {"id": c["id"], "name": c["name"], "source": c["source"], "schemas": c.get("schemas") or "",
           "context": c.get("context") or "", "ssh": ssh_tunnel.public(c.get("ssh")), "error": c.get("error")}
    if c.get("dsn") and psycopg:
        info = conninfo_to_dict(c["dsn"])
        out.update(host=info.get("host", "localhost"), port=info.get("port", "5432"),
                   dbname=info.get("dbname", ""), user=info.get("user", ""), sslmode=info.get("sslmode", ""),
                   has_password=bool(info.get("password")))
    return out


def connections():
    return [public(c) for c in _all_configs()]


def _schemas(c):
    return [s.strip() for s in (c.get("schemas") or DB_SCHEMAS).split(",") if s.strip()] or ["public"]


# ---------------------------------------------------------------- connecting
def _remote(dsn):
    """Database host/port from the connection string (as seen from the SSH server)."""
    info = conninfo_to_dict(dsn)
    host = (info.get("hostaddr") or info.get("host") or "localhost").split(",")[0]
    if host.startswith("/"):
        raise ValueError("A Unix-socket host can't be used through SSH; use localhost instead.")
    return host, int(str(info.get("port") or 5432).split(",")[0])


def _open(c):
    if not available():
        raise RuntimeError("PostgreSQL support needs 'pip install \"psycopg[binary]\"'")
    if c.get("error"):
        raise RuntimeError(c["error"])
    dsn = c["dsn"]
    if c.get("ssh"):
        rhost, rport = _remote(dsn)
        local_port = ssh_tunnel.ensure(c["ssh"], rhost, rport)
        # hostaddr routes the connection into the tunnel; host stays the real name for SSL checks
        dsn = make_conninfo(dsn, host=conninfo_to_dict(dsn).get("host") or "localhost",
                            hostaddr="127.0.0.1", port=str(local_port))
    conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True)
    conn.execute("SET default_transaction_read_only = on")
    conn.execute(f"SET statement_timeout = {int(DB_TIMEOUT_MS)}")
    conn.autocommit = False
    conn.read_only = True
    return conn


def make_dsn(fields, existing_dsn=None):
    """Connection string from form fields. A blank password keeps the saved one."""
    if fields.get("dsn"):
        return fields["dsn"].strip()
    parts = {k: str(fields[k]).strip() for k in ("host", "port", "dbname", "user", "sslmode")
             if fields.get(k) not in (None, "")}
    if not parts.get("dbname"):
        raise ValueError("Database name is required.")
    if fields.get("password"):
        parts["password"] = fields["password"]
    elif existing_dsn:
        old = conninfo_to_dict(existing_dsn).get("password")
        if old:
            parts["password"] = old
    return make_conninfo(**parts)


def make_ssh(fields, existing=None):
    """SSH settings from the form, or None when SSH is off. Blank secrets keep the saved ones."""
    if not fields.get("ssh_enabled"):
        return None
    host = str(fields.get("ssh_host") or "").strip()
    user = str(fields.get("ssh_user") or "").strip()
    if not host:
        raise ValueError("SSH host is required.")
    if not user:
        raise ValueError("SSH user is required.")
    auth = fields.get("ssh_auth") or "password"
    old = existing or {}
    cfg = {"host": host, "port": int(fields.get("ssh_port") or 22), "user": user,
           "password": None, "key_path": None, "key_text": None, "passphrase": None}
    if auth == "password":
        cfg["password"] = fields.get("ssh_password") or old.get("password")
    elif auth == "key_path":
        cfg["key_path"] = str(fields.get("ssh_key_path") or "").strip() or None
        if not cfg["key_path"]:
            raise ValueError("Enter the SSH key file path.")
        cfg["passphrase"] = fields.get("ssh_passphrase") or (old.get("passphrase") if old.get("key_path") else None)
    elif auth == "key_text":
        cfg["key_text"] = fields.get("ssh_key_text") or old.get("key_text")
        if not cfg["key_text"]:
            raise ValueError("Paste the SSH private key.")
        cfg["passphrase"] = fields.get("ssh_passphrase") or (old.get("passphrase") if old.get("key_text") else None)
    # auth == "agent": no secrets; uses ssh-agent and ~/.ssh default keys on the server
    return cfg


def _test(c):
    conn = _open(c)
    try:
        return {"dbname": conn.info.dbname, "user": conn.info.user,
                "server_version": conn.execute("SHOW server_version").fetchone()[0]}
    finally:
        conn.rollback()
        conn.close()


def save(fields, cid=None, force=False):
    """Create or update a saved connection. Tests it first unless force=True."""
    name = str(fields.get("name") or "").strip()
    if not name:
        raise ValueError("Give the connection a name.")
    if not re.fullmatch(r"[\w .\-]{1,60}", name):
        raise ValueError("Use letters, numbers, spaces, dots, dashes or underscores in the name (max 60).")
    if cid == ENV_ID:
        raise ValueError("The DATABASE_URL connection is set in .env and can't be edited here.")
    old = store.get(cid) if cid else None
    if cid and not old:
        raise KeyError("That connection no longer exists.")
    c = {"id": cid or "new", "name": name, "dsn": make_dsn(fields, old and old["dsn"]),
         "ssh": make_ssh(fields, old and old["ssh"]), "schemas": str(fields.get("schemas") or "").strip(),
         "context": str(fields.get("context") or "").strip(), "source": "saved"}
    if not force:
        _test(c)
    cid = store.save(c["name"], c["dsn"], c["ssh"], c["schemas"], c["context"], cid=cid)
    _drop(cid, old)
    return status(cid)


def _drop(cid, old):
    """Forget cached schema and close the old tunnel after an edit or delete."""
    with _lock:
        _schema_cache.pop(cid, None)
    if old and old.get("ssh") and old.get("dsn"):
        try:
            ssh_tunnel.close(old["ssh"], *_remote(old["dsn"]))
        except Exception:
            pass


def delete(cid):
    if cid == ENV_ID:
        raise ValueError("The DATABASE_URL connection is set in .env; remove it there.")
    old = store.get(cid)
    store.delete(cid)
    _drop(cid, old)


def status(cid):
    try:
        c = config(cid)
    except RuntimeError as e:  # saved secret can't be decrypted
        return {"id": cid, "ok": False, "error": str(e), "available": available(),
                "ssh_available": ssh_tunnel.available()}
    st = public(c)
    st.update(ok=False, available=available(), ssh_available=ssh_tunnel.available())
    try:
        st.update(_test(c), ok=True)
        if c.get("ssh"):
            st["ssh"].update(ssh_tunnel.state(c["ssh"], *_remote(c["dsn"])))
    except Exception as e:
        st["error"] = str(e).strip()
    return st


# ---------------------------------------------------------------- schema
SCHEMA_COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, t.table_type, c.column_name, c.data_type, c.is_nullable
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = ANY(%s) AND t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""
SCHEMA_KEYS_SQL = """
SELECT n.nspname, cl.relname, a.attname, con.contype, fn.nspname, fcl.relname, fa.attname
FROM pg_constraint con
JOIN pg_class cl ON cl.oid = con.conrelid
JOIN pg_namespace n ON n.oid = cl.relnamespace
CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
LEFT JOIN pg_class fcl ON fcl.oid = con.confrelid
LEFT JOIN pg_namespace fn ON fn.oid = fcl.relnamespace
LEFT JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = con.confkey[k.ord]
WHERE n.nspname = ANY(%s) AND con.contype IN ('p', 'f')
"""
SCHEMA_ROWS_SQL = """
SELECT n.nspname, c.relname, c.reltuples::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p')
"""


def schema(cid, refresh=False):
    """Compact text description of tables, columns, keys and row estimates."""
    c = config(cid)
    fp = (c.get("dsn"), repr(c.get("ssh")), c.get("schemas"))
    with _lock:
        hit = _schema_cache.get(cid)
        if hit and not refresh and hit["fp"] == fp and time.time() - hit["at"] < SCHEMA_CACHE_SECS:
            return {"text": hit["text"], "tables": hit["tables"]}
    schemas = _schemas(c)
    conn = _open(c)
    try:
        cols = conn.execute(SCHEMA_COLUMNS_SQL, (schemas,)).fetchall()
        keys = conn.execute(SCHEMA_KEYS_SQL, (schemas,)).fetchall()
        counts = {(s, t): n for s, t, n in conn.execute(SCHEMA_ROWS_SQL, (schemas,)).fetchall()}
    finally:
        conn.rollback()
        conn.close()

    marks = {}
    for s, t, col, kind, fs, ft, fc in keys:
        tag = "PK" if kind == "p" else f"-> {fs}.{ft}.{fc}"
        marks.setdefault((s, t, col), []).append(tag)

    tables, order = {}, []
    for s, t, ttype, col, dtype, nullable in cols:
        key = (s, t)
        if key not in tables:
            order.append(key)
            n = counts.get(key)
            extra = " (view)" if ttype == "VIEW" else (f" (~{n:,} rows)" if n is not None and n >= 0 else "")
            tables[key] = [f"{s}.{t}{extra}"]
        flags = marks.get((s, t, col), []) + ([] if nullable == "YES" else ["not null"])
        tables[key].append(f"  {col} {dtype}" + (f"  [{', '.join(flags)}]" if flags else ""))

    out, used = [], 0
    for i, key in enumerate(order):
        block = "\n".join(tables[key])
        if used + len(block) > DB_SCHEMA_CHARS:
            out.append(f"... schema truncated: {len(order) - i} more table(s) not shown: "
                       + ", ".join(f"{s}.{t}" for s, t in order[i:i + 50]))
            break
        out.append(block)
        used += len(block) + 1
    text = "\n".join(out) if out else f"(no tables found in schema(s): {', '.join(schemas)})"
    with _lock:
        _schema_cache[cid] = {"at": time.time(), "fp": fp, "text": text, "tables": len(order)}
    return {"text": text, "tables": len(order)}


# ---------------------------------------------------------------- queries
def _strip_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql).strip()


def check_sql(sql):
    clean = _strip_comments(sql).rstrip(";").strip()
    if not clean:
        raise ValueError("The query is empty.")
    first = re.split(r"\s+", clean.lstrip("("), 1)[0].lower()
    if first not in ALLOWED_FIRST_WORDS:
        raise ValueError("Only read-only queries can be run (SELECT, WITH, EXPLAIN, SHOW, VALUES or TABLE).")
    return sql.strip().rstrip(";").strip()


def _cell(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v[:2000] if isinstance(v, str) else v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return "\\x" + b[:100].hex() + ("..." if len(b) > 100 else "")
    return str(v)[:2000]


def resolve_target(sql, cids):
    """Pick which of the chat's connections a query is for, using a '-- db: name' line if present."""
    cids = [c for c in cids if c]
    if not cids:
        raise ValueError("No database is turned on for this chat.")
    configs = []
    for cid in cids:
        try:
            configs.append(config(cid))
        except KeyError:
            pass
    if not configs:
        raise ValueError("The databases selected for this chat no longer exist. Pick one again.")
    m = DB_TAG_RE.search(sql)
    if m:
        want = m.group(1).strip().strip("`'\"").lower()
        for c in configs:
            if want in (c["name"].lower(), c["id"].lower()):
                return c
        raise ValueError(f"No database named '{m.group(1).strip()}' in this chat. "
                         f"Use one of: {', '.join(c['name'] for c in configs)}.")
    if len(configs) == 1:
        return configs[0]
    raise ValueError("This chat has several databases. Add '-- db: <name>' as the first line of the query, "
                     f"using one of: {', '.join(c['name'] for c in configs)}.")


def run_query(sql, cids):
    """Run one read-only query on the right connection. Returns columns, rows (capped) and timing."""
    c = resolve_target(sql, cids)
    sql = check_sql(sql)
    t0 = time.time()
    conn = _open(c)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, prepare=True)  # prepared => exactly one statement allowed
            if cur.description is None:
                columns, rows = [], []
            else:
                columns = [d.name for d in cur.description]
                rows = cur.fetchmany(DB_MAX_ROWS + 1)
    finally:
        conn.rollback()
        conn.close()
    truncated = len(rows) > DB_MAX_ROWS
    rows = [[_cell(v) for v in r] for r in rows[:DB_MAX_ROWS]]
    return {"db_id": c["id"], "db_name": c["name"], "columns": columns, "rows": rows, "row_count": len(rows),
            "truncated": truncated, "ms": int((time.time() - t0) * 1000)}


def _md(v):
    s = "NULL" if v is None else str(v)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")[:300]


def result_text(sql, res=None, error=None, db_name=None):
    """Format a result (or error) as text for the model, capped at DB_RESULT_CHARS."""
    head = (f"Database: {db_name}\n" if db_name else "") + f"```sql\n{sql.strip()}\n```\n"
    if error:
        return head + f"Error: {error}"
    if not res["columns"]:
        return head + "The query ran but returned no columns."
    lines = ["| " + " | ".join(_md(c) for c in res["columns"]) + " |",
             "|" + "---|" * len(res["columns"])]
    used, shown = sum(len(x) for x in lines), 0
    for r in res["rows"]:
        line = "| " + " | ".join(_md(v) for v in r) + " |"
        if used + len(line) > DB_RESULT_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    n = res["row_count"]
    if n == 0:
        summary = "Returned 0 rows."
    elif shown < n or res["truncated"]:
        more = f"{n}+" if res["truncated"] else str(n)
        summary = (f"Returned {more} rows; showing the first {shown}. Use aggregates, WHERE or LIMIT "
                   "for a smaller result if you need the rest.")
    else:
        summary = f"Returned {n} row(s)."
    return head + summary + ("\n" + "\n".join(lines) if n else "")


def system_prompt(cids):
    """Instructions + each database's notes and schema, for chats with databases turned on."""
    sections, names = [], []
    for cid in cids:
        try:
            c = config(cid)
        except KeyError:
            continue
        names.append(c["name"])
        try:
            st = schema(cid)
            body = (f"Schema ({st['tables']} table(s); columns are name and type, [PK] primary key, "
                    f"[-> table.column] foreign key):\n{st['text']}")
        except Exception as e:
            body = f"(This database is currently unavailable: {str(e).strip()[:300]})"
        notes = f"Notes about this database:\n{c['context']}\n\n" if c.get("context") else ""
        sections.append(f"=== Database: {c['name']} (PostgreSQL) ===\n{notes}{body}")
    if not sections:
        return ""
    multi = len(sections) > 1
    target = (f"\n- This chat has {len(sections)} databases ({', '.join(names)}). Start every query with a line "
              "'-- db: <name>' naming the database it is for. A query can only use one database; to combine "
              "data from several, run one query per database and combine the results yourself.") if multi else ""
    return f"""You can read data from {'these PostgreSQL databases' if multi else 'a PostgreSQL database'}. To look something up, write a read-only SQL query in a ```sql code block. The app runs it and sends you the results in the next message, then you answer from those results.

Rules:
- Only SELECT, WITH, EXPLAIN, SHOW, VALUES or TABLE queries run, one statement per block, in a read-only transaction with a {DB_TIMEOUT_MS // 1000}s timeout.
- At most {DB_MAX_ROWS} rows come back. Prefer aggregates (COUNT, SUM, GROUP BY), filters and LIMIT over fetching raw rows.
- Use only the tables and columns in the schema below. Use schema-qualified names when unsure.
- After writing a query, stop and wait for the results. Never make up results.
- If a query fails, read the error, fix the query and try again.
- When you have what you need, answer the question directly without writing more SQL.{target}

""" + "\n\n".join(sections)
