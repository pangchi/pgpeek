"""
Saved database connections, kept in a local SQLite file.

Connection strings (with passwords) and SSH settings (passwords, pasted keys, passphrases)
are encrypted with Fernet (AES-128-CBC + HMAC). The key comes from APP_SECRET_KEY, or is
generated once into secret.key next to this file. Keep secret.key private and back it up:
without it, saved passwords can't be decrypted and must be re-entered.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DB = os.getenv("CONFIG_DB", os.path.join(BASE, "connections.sqlite"))
KEY_FILE = os.getenv("SECRET_KEY_FILE", os.path.join(BASE, "secret.key"))

_lock = threading.Lock()
_fernet = None


def _cipher():
    global _fernet
    if _fernet:
        return _fernet
    key = os.getenv("APP_SECRET_KEY", "").strip().encode()
    if not key:
        if os.path.exists(KEY_FILE):
            key = open(KEY_FILE, "rb").read().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
    _fernet = Fernet(key)
    return _fernet


def _db():
    conn = sqlite3.connect(CONFIG_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS connections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        secret BLOB NOT NULL,          -- encrypted JSON: {"dsn": ..., "ssh": {...} | null}
        schemas TEXT NOT NULL DEFAULT '',
        context TEXT NOT NULL DEFAULT '',
        created REAL NOT NULL,
        updated REAL NOT NULL)""")
    return conn


def _row(r):
    try:
        secret = json.loads(_cipher().decrypt(r["secret"]))
    except InvalidToken:
        raise RuntimeError(f"Can't decrypt saved connection '{r['name']}': secret.key or APP_SECRET_KEY "
                           "has changed. Re-enter its password, or restore the original key.")
    return {"id": r["id"], "name": r["name"], "dsn": secret["dsn"], "ssh": secret.get("ssh"),
            "schemas": r["schemas"], "context": r["context"], "updated": r["updated"]}


def _seal(dsn, ssh):
    return _cipher().encrypt(json.dumps({"dsn": dsn, "ssh": ssh}).encode())


def list_all():
    with _lock, _db() as conn:
        rows = conn.execute("SELECT * FROM connections ORDER BY name COLLATE NOCASE").fetchall()
    out = []
    for r in rows:
        try:
            out.append(_row(r))
        except RuntimeError as e:  # still list it, so the user can fix or delete it
            out.append({"id": r["id"], "name": r["name"], "dsn": None, "ssh": None, "schemas": r["schemas"],
                        "context": r["context"], "updated": r["updated"], "error": str(e)})
    return out


def get(cid):
    with _lock, _db() as conn:
        r = conn.execute("SELECT * FROM connections WHERE id = ?", (cid,)).fetchone()
    return _row(r) if r else None


def save(name, dsn, ssh, schemas="", context="", cid=None):
    now = time.time()
    try:
        with _lock, _db() as conn:
            if cid:
                cur = conn.execute("UPDATE connections SET name=?, secret=?, schemas=?, context=?, updated=? "
                                   "WHERE id=?", (name, _seal(dsn, ssh), schemas, context, now, cid))
                if not cur.rowcount:
                    raise KeyError(cid)
            else:
                cid = uuid.uuid4().hex[:12]
                conn.execute("INSERT INTO connections VALUES (?,?,?,?,?,?,?)",
                             (cid, name, _seal(dsn, ssh), schemas, context, now, now))
    except sqlite3.IntegrityError:
        raise ValueError(f"A connection named '{name}' already exists.")
    return cid


def delete(cid):
    with _lock, _db() as conn:
        return conn.execute("DELETE FROM connections WHERE id = ?", (cid,)).rowcount > 0
