"""
pgPeek: chat with your PostgreSQL databases and files using any OpenAI-compatible model.

Configure with environment variables (or a .env file):
    OPENAI_API_KEY   - API key
    OPENAI_BASE_URL  - e.g. https://example.com/v1
    OPENAI_MODEL     - default model name
    SYSTEM_PROMPT    - optional default system prompt
    UPLOAD_DIR       - where uploaded files are stored (default ./uploads)
    MAX_UPLOAD_MB    - per-file upload limit (default 20)
    MAX_TABLE_UPLOAD_MB - per-file limit for CSV/TSV/Excel loaded as SQL tables (default 200)
    MAX_TEXT_CHARS   - max characters of text sent per file or zip (default 400000, ~100k tokens)
    MAX_SEND_CHARS   - max characters per request; larger sends are split into parts (default 100000)
    PART_NOTE_TOKENS - max tokens for the notes taken from each part (default 2000)
    ZIP_MAX_FILES    - max files read from one .zip (default 300)
    ZIP_MAX_TOTAL_MB - max total uncompressed size of one .zip (default 100)
    ZIP_MAX_IMAGES   - max images sent from one .zip (default 10)
    DATABASE_URL     - optional PostgreSQL connection shown alongside saved ones (see db.py)
    CONFIG_DB        - SQLite file for saved connections (default ./connections.sqlite)
    APP_SECRET_KEY   - Fernet key for saved passwords (default: generated into ./secret.key)
    ALLOWED_EXTENSIONS - optional comma list overriding the allowed upload types, e.g. ".png,.pdf,.csv"
"""
import base64
import csv
import io
import gc
import json
import mimetypes
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
import zipfile
import posixpath

import openai

import db
import filesql
from version import __version__ as VERSION
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory, stream_with_context
from werkzeug.utils import secure_filename

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_KEY = os.getenv("OPENAI_API_KEY", "your_api_key")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://example.com")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant.")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", 20))
# CSV/TSV/Excel go into DuckDB rather than the model's context, so they can be much larger.
MAX_TABLE_UPLOAD_MB = int(os.getenv("MAX_TABLE_UPLOAD_MB", 200)) if filesql.available() else MAX_UPLOAD_MB
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", 400_000))   # ~100k tokens
MAX_SEND_CHARS = int(os.getenv("MAX_SEND_CHARS", 100_000))      # per request to the API
PART_NOTE_TOKENS = int(os.getenv("PART_NOTE_TOKENS", 2000))     # max reply length for each part's notes
ZIP_MAX_FILES = int(os.getenv("ZIP_MAX_FILES", 300))
ZIP_MAX_TOTAL_MB = int(os.getenv("ZIP_MAX_TOTAL_MB", 100))
ZIP_MAX_IMAGES = int(os.getenv("ZIP_MAX_IMAGES", 10))

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = max(MAX_UPLOAD_MB, MAX_TABLE_UPLOAD_MB) * 1024 * 1024 + 64 * 1024  # + form overhead
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Allowed types, per the model's stated support: images, documents, data files and code.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
DOC_EXTS = {".pdf", ".docx", ".xlsx"}                      # converted to text on the server
ARCHIVE_EXTS = {".zip"}                                    # extracted; members processed like uploads
TEXT_EXTS = {
    # documents / data
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".log",
    # code
    ".py", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss", ".vue", ".svelte",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".java", ".kt", ".swift", ".go", ".rs", ".rb", ".php",
    ".pl", ".lua", ".r", ".m", ".scala", ".dart", ".sh", ".bash", ".bat", ".ps1", ".sql", ".ino", ".v",
    ".sv", ".vhd", ".asm", ".s", ".make", ".cmake", ".gradle",
}
_env_allowed = os.getenv("ALLOWED_EXTENSIONS", "").strip()
ALLOWED_EXTS = (
    {("." + e.strip().lower().lstrip(".")) for e in _env_allowed.split(",") if e.strip()}
    if _env_allowed else IMAGE_EXTS | DOC_EXTS | TEXT_EXTS | ARCHIVE_EXTS
)
SPECIAL_NAMES = {"dockerfile", "makefile"}                 # code files with no extension
SKIP_DIRS = {"__macosx", ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
ID_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------- uploads
def extract_text(path, name, mime):
    """Return text content of a document, or raise ValueError if unsupported."""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF support needs 'pip install pypdf'")
        reader = PdfReader(path)
        return "\n\n".join(f"[Page {i + 1}]\n{p.extract_text() or ''}" for i, p in enumerate(reader.pages))
    if ext == ".docx":
        try:
            import docx
        except ImportError:
            raise ValueError("Word support needs 'pip install python-docx'")
        d = docx.Document(path)
        out = [p.text for p in d.paragraphs]
        for t in d.tables:
            out += [" | ".join(c.text.strip() for c in row.cells) for row in t.rows]
        return "\n".join(out)
    if ext == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise ValueError("Excel support needs 'pip install openpyxl'")
        import csv, io
        wb = load_workbook(path, read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            buf = io.StringIO()
            w = csv.writer(buf)
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    w.writerow(["" if v is None else v for v in row])
            out.append(f"[Sheet: {ws.title}]\n{buf.getvalue()}")
        return "\n".join(out)
    with open(path, "rb") as fh:  # only what can be kept: MAX_TEXT_CHARS characters, up to 4 bytes each
        raw = fh.read((MAX_TEXT_CHARS + 1) * 4)
    if b"\x00" in raw[:4096]:
        raise ValueError("This looks like a binary file, not text.")
    cut = os.path.getsize(path) > len(raw)
    for trim in (0, 1, 2, 3) if cut else (0,):  # a cut can split a UTF-8 character
        try:
            return raw[:len(raw) - trim].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def load_meta(fid):
    if not isinstance(fid, str) or not ID_RE.match(fid):
        return None
    try:
        with open(os.path.join(UPLOAD_DIR, fid, "meta.json")) as fh:
            return json.load(fh)
    except OSError:
        return None


def image_to_sendable(path, ext):
    """Return (path, mime) for an image, converting BMP to PNG if Pillow is available."""
    if ext == ".bmp":
        try:
            from PIL import Image
        except ImportError:
            return path, "image/bmp"
        png = os.path.splitext(path)[0] + ".png"
        Image.open(path).save(png, "PNG")
        os.remove(path)
        return png, "image/png"
    return path, mimetypes.guess_type(path)[0] or "image/" + ext.lstrip(".").replace("jpg", "jpeg")


def process_zip(zpath, folder):
    """Extract a zip safely and turn its allowed members into one text block plus images.

    Nothing is extracted by its archive path, so '../' entries can't escape the folder.
    Returns (text, images, stats) where images is a list of {file, mime, name}.
    """
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile:
        raise ValueError("This isn't a valid zip file.")
    members = [i for i in zf.infolist() if not i.is_dir()]
    if any(i.flag_bits & 0x1 for i in members):
        raise ValueError("Password-protected zip files aren't supported.")

    work = os.path.join(folder, "members")
    os.makedirs(work, exist_ok=True)
    budget = ZIP_MAX_TOTAL_MB * 1024 * 1024
    included, skipped, texts, images = [], [], [], []

    for idx, info in enumerate(sorted(members, key=lambda i: i.filename)):
        arc = info.filename.replace("\\", "/")
        parts = [p for p in arc.split("/") if p not in ("", ".", "..")]
        base = parts[-1] if parts else ""
        ext = posixpath.splitext(base)[1].lower()
        if (not base or base.startswith("._") or base == ".DS_Store"
                or any(p.lower() in SKIP_DIRS for p in parts[:-1])):
            continue  # OS/tooling junk: ignore silently
        shown = "/".join(parts)
        if len(included) >= ZIP_MAX_FILES:
            skipped.append((shown, "file limit reached")); continue
        if ext in ARCHIVE_EXTS:
            skipped.append((shown, "nested zip")); continue
        if ext not in ALLOWED_EXTS and base.lower() not in SPECIAL_NAMES:
            skipped.append((shown, "type not allowed")); continue
        if info.file_size > MAX_UPLOAD_MB * 1024 * 1024:
            skipped.append((shown, f"over {MAX_UPLOAD_MB} MB")); continue
        if info.file_size > budget:
            skipped.append((shown, "zip size limit reached")); continue
        budget -= info.file_size

        tmp = os.path.join(work, f"{idx}{ext}")
        with zf.open(info) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)

        if ext in IMAGE_EXTS:
            if len(images) >= ZIP_MAX_IMAGES:
                os.remove(tmp); skipped.append((shown, f"over {ZIP_MAX_IMAGES} images")); continue
            try:
                ipath, imime = image_to_sendable(tmp, ext)
            except Exception:
                skipped.append((shown, "unreadable image")); continue
            images.append({"file": os.path.relpath(ipath, folder), "mime": imime, "name": shown})
            included.append(shown)
            continue

        try:
            body = extract_text(tmp, base, mimetypes.guess_type(base)[0] or "")
        except Exception as e:
            skipped.append((shown, str(e))); continue
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if not body.strip():
            skipped.append((shown, "no readable text")); continue
        texts.append((shown, body))
        included.append(shown)

    zf.close()

    # Share the text budget fairly: smallest files first, each gets at most an equal share
    # of what's left, so small files stay whole and only the largest ones are trimmed.
    alloc, left = {}, MAX_TEXT_CHARS
    order = sorted(range(len(texts)), key=lambda i: len(texts[i][1]))
    for k, i in enumerate(order):
        alloc[i] = min(len(texts[i][1]), left // (len(order) - k))
        left -= alloc[i]
    sections, trimmed = [], []
    for i, (shown, body) in enumerate(texts):
        if alloc[i] < len(body):
            trimmed.append(shown)
            body = body[:alloc[i]] + f"\n[... truncated: showing {alloc[i]:,} of {len(body):,} characters]"
        sections.append(f"=== {shown} ===\n{body}")
    truncated = bool(trimmed)
    header = [f"Archive contents ({len(included)} file(s) included):"] + [
        f"  {n}" + ("  (truncated)" if n in trimmed else "") for n in included]
    if skipped:
        header += [f"Skipped ({len(skipped)}):"] + [f"  {n} - {why}" for n, why in skipped[:100]]
        if len(skipped) > 100:
            header.append(f"  ... and {len(skipped) - 100} more")
    text = "\n".join(header) + ("\n\n" + "\n\n".join(sections) if sections else "")
    stats = {"files": len(included), "skipped": len(skipped), "images": len(images), "trimmed": len(trimmed)}
    return text, images, stats, truncated


@app.errorhandler(413)
def too_large(_):
    return jsonify(error=f"File is larger than the upload limit ({limit_text()})."), 413


def limit_text():
    if MAX_TABLE_UPLOAD_MB > MAX_UPLOAD_MB:
        return f"{MAX_UPLOAD_MB} MB, or {MAX_TABLE_UPLOAD_MB} MB for CSV, TSV and Excel"
    return f"{MAX_UPLOAD_MB} MB"


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file received."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTS and f.filename.lower() not in SPECIAL_NAMES:
        return jsonify(error=f"{ext or 'Files without an extension'} isn't an allowed file type."), 400
    fid = uuid.uuid4().hex
    folder = os.path.join(UPLOAD_DIR, fid)
    with _uploads_lock:   # while processing, Storage shows it as uploading and it can't be deleted
        _in_progress[fid] = (f.filename, time.time())
    try:
        os.makedirs(folder)
        return _process_upload(f, ext, fid, folder)
    except Exception as e:  # e.g. the folder was removed by hand mid-way: answer cleanly, don't leave half a folder
        app.logger.exception("upload %s failed", fid)
        remove_tree(folder)
        return jsonify(error=f"The upload couldn't be processed: {e}"), 500
    finally:
        with _uploads_lock:
            _in_progress.pop(fid, None)


def _process_upload(f, ext, fid, folder):
    name = secure_filename(f.filename) or "file"
    path = os.path.join(folder, name)
    f.save(path)
    mime = f.mimetype if f.mimetype and f.mimetype != "application/octet-stream" else (
        mimetypes.guess_type(name)[0] or "application/octet-stream")
    meta = {"id": fid, "name": f.filename, "file": name, "mime": mime,
            "size": os.path.getsize(path), "kind": "image", "truncated": False, "uploaded": time.time()}
    tabular = filesql.available() and filesql.is_tabular(name)
    limit_mb = MAX_TABLE_UPLOAD_MB if tabular else MAX_UPLOAD_MB
    if meta["size"] > limit_mb * 1024 * 1024:
        remove_tree(folder)
        return jsonify(error=f"File is larger than the {limit_mb} MB limit for this type ({limit_text()})."), 413

    if ext in ARCHIVE_EXTS:
        try:
            text, images, stats, truncated = process_zip(path, folder)
        except Exception as e:
            remove_tree(folder)
            return jsonify(error=str(e)), 400
        if not stats["files"]:
            remove_tree(folder)
            return jsonify(error="The zip has no files of an allowed type."), 400
        meta.update(kind="zip", mime="application/zip", images=images, stats=stats, truncated=truncated)
        with open(os.path.join(folder, "extracted.txt"), "w", encoding="utf-8") as fh:
            fh.write(text)
    elif ext in IMAGE_EXTS:
        meta["mime"] = mimetypes.guess_type(name)[0] or mime
        if ext == ".bmp":  # most APIs reject BMP, so convert to PNG when Pillow is available
            try:
                from PIL import Image
                png = os.path.splitext(name)[0] + ".png"
                Image.open(path).save(os.path.join(folder, png), "PNG")
                os.remove(path)
                meta.update(file=png, mime="image/png", size=os.path.getsize(os.path.join(folder, png)))
            except ImportError:
                pass
            except Exception:
                remove_tree(folder)
                return jsonify(error="This BMP image couldn't be read."), 400
    else:
        tables = []
        if tabular:
            try:
                tables = filesql.prepare(path, name, folder)
            except Exception:
                tables = []  # not loadable as a table: fall back to text below
        if tables:
            # The model gets a preview plus SQL access, so only the first lines are kept as text.
            text = filesql.head_text(folder, tables)
            filesql.cleanup(folder)
            meta["tables"] = tables
        else:
            if meta["size"] > MAX_UPLOAD_MB * 1024 * 1024:
                remove_tree(folder)
                return jsonify(error=f"This file couldn't be read as a table, and other files are limited to "
                                     f"{MAX_UPLOAD_MB} MB. Check it's a valid CSV/TSV/Excel file."), 400
            try:
                text = extract_text(path, name, mime)
            except Exception as e:
                remove_tree(folder)
                return jsonify(error=str(e)), 400
        if not text.strip():
            remove_tree(folder)
            return jsonify(error="No readable text found in this file (scanned PDFs need OCR)."), 400
        meta["kind"] = "text"
        meta["truncated"] = len(text) > MAX_TEXT_CHARS
        meta["chars"] = len(text)
        with open(os.path.join(folder, "extracted.txt"), "w", encoding="utf-8") as fh:
            fh.write(text[:MAX_TEXT_CHARS])

    with open(os.path.join(folder, "meta.json"), "w") as fh:
        json.dump(meta, fh)
    out = {k: meta[k] for k in ("id", "name", "mime", "size", "kind", "truncated", "stats") if k in meta}
    if meta.get("tables"):
        out["tables"] = [{"rows": t["rows"], "columns": len(t["columns"]), "sheet": t["sheet"]} for t in meta["tables"]]
    return jsonify(out)


# ---------------------------------------------------------------- stored uploads (Storage panel)
_uploads_lock = threading.Lock()
_in_progress = {}   # file id -> (original name, start time) for uploads still being processed


def remove_tree(folder, attempts=5):
    """Delete a folder, retrying: on Windows, antivirus or indexing often holds new files open for a moment.

    Clears read-only flags as it goes. Returns None when the folder is gone, else the last error.
    """
    def retry_writable(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass
    handler = {"onexc": retry_writable} if sys.version_info >= (3, 12) else {"onerror": retry_writable}
    err = None
    for i in range(attempts):
        if not os.path.exists(folder):
            return None
        try:
            shutil.rmtree(folder, **handler)
        except Exception as e:
            err = e
        if not os.path.exists(folder):
            return None
        gc.collect()                    # release any file handles this process still holds
        time.sleep(0.15 * 2 ** i)       # 0.15 s, 0.3 s, 0.6 s, 1.2 s
    return err or OSError("the folder or a file in it is in use")


def _delete_error_text(folder, err):
    left = [os.path.relpath(os.path.join(r, n), folder) for r, _, fs in os.walk(folder) for n in fs]
    what = f" ({', '.join(left[:3])}{'…' if len(left) > 3 else ''} still there)" if left else ""
    return (f"Couldn't delete{what}: {err}. Another program may have it open (antivirus, search indexing, "
            "a file manager window, or Excel); try again in a moment.")
def _folder_bytes(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@app.get("/api/files")
def list_files():
    """Every upload on the server: name, original size, size on disk (incl. tables), upload time, tables."""
    out, total = [], 0
    try:
        names = os.listdir(UPLOAD_DIR)
    except OSError:
        names = []
    for fid in names:
        folder = os.path.join(UPLOAD_DIR, fid)
        if not ID_RE.match(fid) or not os.path.isdir(folder):
            continue
        with _uploads_lock:
            busy = _in_progress.get(fid)
        disk = _folder_bytes(folder)
        meta = load_meta(fid)
        if busy:
            total += disk
            out.append({"id": fid, "name": busy[0], "kind": "uploading", "size": 0, "disk": disk,
                        "uploaded": busy[1], "processing": True, "tables": []})
            continue
        if not meta and not any(fs for _, _, fs in os.walk(folder)) and time.time() - os.path.getmtime(folder) > 60:
            if remove_tree(folder, attempts=1) is None:   # an empty leftover folder: nothing to show, just tidy it
                continue
        total += disk
        if meta:
            uploaded = meta.get("uploaded") or os.path.getmtime(os.path.join(folder, "meta.json"))
            out.append({"id": fid, "name": meta["name"], "kind": meta["kind"], "size": meta.get("size", 0),
                        "disk": disk, "uploaded": uploaded, "stats": meta.get("stats"),
                        "tables": [{"rows": t["rows"], "columns": len(t["columns"]), "sheet": t["sheet"]}
                                   for t in meta.get("tables") or []]})
        else:  # an upload that never finished, or a delete that couldn't remove everything
            out.append({"id": fid, "name": "(incomplete upload)", "kind": "incomplete", "size": 0, "disk": disk,
                        "uploaded": os.path.getmtime(folder), "incomplete": True, "tables": []})
    out.sort(key=lambda f: f["uploaded"], reverse=True)
    return jsonify(files=out, total_disk=total)


def delete_uploads(ids):
    """Delete uploads. Already-missing ones count as deleted; ones that can't be removed are reported."""
    deleted, failed, freed = [], [], 0
    for fid in ids:
        if not isinstance(fid, str) or not ID_RE.match(fid):
            continue
        with _uploads_lock:
            busy = fid in _in_progress
        if busy:
            failed.append({"id": fid, "error": "It's still uploading. Try again when it has finished."})
            continue
        folder = os.path.join(UPLOAD_DIR, fid)
        if not os.path.isdir(folder):
            deleted.append(fid)
            continue
        size = _folder_bytes(folder)
        err = remove_tree(folder)
        if err is None:
            deleted.append(fid)
            freed += size
        else:
            failed.append({"id": fid, "error": _delete_error_text(folder, err)})
            freed += size - _folder_bytes(folder)
    return {"deleted": deleted, "failed": failed, "freed": freed}


@app.delete("/api/files/<fid>")
def delete_file(fid):
    if not ID_RE.match(fid) or not os.path.isdir(os.path.join(UPLOAD_DIR, fid)):
        return jsonify(error="That file no longer exists."), 404
    r = delete_uploads([fid])
    if r["failed"]:
        return jsonify(dict(r, error=r["failed"][0]["error"])), 409
    return jsonify(r)


@app.post("/api/files/delete")
def delete_files():
    ids = (request.get_json(force=True) or {}).get("ids") or []
    return jsonify(delete_uploads(ids[:5000] if isinstance(ids, list) else []))


# ---------------------------------------------------------------- user guide (Help link)
GUIDE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "USER_GUIDE.md")
GUIDE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>pgPeek user guide</title>
<style>
:root{--bg:#fff;--ink:#1b2430;--muted:#66727f;--line:#dfe3e8;--code:#f4f6f8;--accent:#2f6f8f}
@media (prefers-color-scheme:dark){:root{--bg:#141a21;--ink:#e3e8ee;--muted:#8c99a6;--line:#2a3440;--code:#1d252f;--accent:#5ba3c6}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:820px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:28px;margin:0 0 12px}h2{font-size:21px;margin:36px 0 10px;padding-top:12px;border-top:1px solid var(--line)}
a{color:var(--accent)}code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:.9em}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto}pre code{padding:0}
table{border-collapse:collapse;margin:10px 0;font-size:15px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}th{background:var(--code)}
blockquote{margin:12px 0;padding:8px 14px;border-left:3px solid var(--accent);background:var(--code);border-radius:0 8px 8px 0}
#raw{white-space:pre-wrap}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js"></script>
</head><body><main id="doc">Loading…</main>
<footer style="max-width:820px;margin:0 auto;padding:0 20px 40px;color:var(--muted);font-size:13px">pgPeek __VERSION__</footer>
<script>
fetch("guide.md").then(r => r.text()).then(text => {
  const doc = document.getElementById("doc");
  if (!window.marked || !window.DOMPurify) { doc.innerHTML = '<pre id="raw"></pre>'; doc.firstChild.textContent = text; return; }
  doc.innerHTML = DOMPurify.sanitize(marked.parse(text));
  // GitHub-style heading ids so the contents links work
  doc.querySelectorAll("h1,h2,h3").forEach(h => { h.id = h.textContent.trim().toLowerCase().replace(/[^a-z0-9 -]/g, "").replace(/ /g, "-"); });
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
});
</script></body></html>"""


@app.get("/guide")
def guide():
    return Response(GUIDE_PAGE.replace("__VERSION__", VERSION), mimetype="text/html")


@app.get("/guide.md")
def guide_md():
    try:
        with open(GUIDE_FILE, encoding="utf-8") as fh:
            return Response(fh.read(), mimetype="text/markdown; charset=utf-8")
    except OSError:
        abort(404)


@app.get("/files/<fid>")
def get_file(fid):
    meta = load_meta(fid)
    if not meta:
        abort(404)
    return send_from_directory(os.path.join(UPLOAD_DIR, fid), meta["file"], mimetype=meta["mime"],
                               download_name=meta["name"])


def chat_file_ids(raw):
    """Ids of tabular (CSV/TSV/Excel) attachments in a chat, in message order."""
    ids = []
    for m in raw:
        if m.get("role") != "user":
            continue
        for a in m.get("attachments") or []:
            fid = a.get("id") if isinstance(a, dict) else a
            if isinstance(fid, str) and fid not in ids:
                ids.append(fid)
    return ids


def file_tables(ids):
    """Chat-level file tables (empty if DuckDB isn't installed)."""
    if not filesql.available():
        return []
    return filesql.chat_tables([i for i in ids if isinstance(i, str)], load_meta, UPLOAD_DIR)


def collect_attachments(msg, ftables=None):
    """Return (docs, images) for a message: docs = [(label, text)], images = [data URLs].

    ftables maps file id -> its SQL tables; those files are sent as a short preview, not in full.
    """
    docs, images = [], []
    for a in msg.get("attachments") or []:
        fid = a.get("id") if isinstance(a, dict) else a
        meta = load_meta(fid)
        if not meta:
            name = a.get("name", fid) if isinstance(a, dict) else fid
            docs.append((name, "[This attachment is no longer available]"))
            continue
        folder = os.path.join(UPLOAD_DIR, fid)
        if meta["kind"] == "image":
            with open(os.path.join(folder, meta["file"]), "rb") as fh:
                images.append(f"data:{meta['mime']};base64,{base64.b64encode(fh.read()).decode()}")
            continue
        with open(os.path.join(folder, "extracted.txt"), encoding="utf-8") as fh:
            body = fh.read()
        if meta["kind"] == "zip":
            label = f"Zip archive: {meta['name']}" + (" (text truncated)" if meta.get("truncated") else "")
            for im in meta.get("images", []):
                with open(os.path.join(folder, im["file"]), "rb") as fh:
                    images.append(f"data:{im['mime']};base64,{base64.b64encode(fh.read()).decode()}")
        elif ftables and ftables.get(fid):
            label = meta["name"] + " (preview)"
            body = filesql.preview(body, ftables[fid])
        else:
            label = meta["name"] + (" (truncated)" if meta.get("truncated") else "")
        docs.append((label, body))
    return docs, images


def fmt_doc(label, body):
    return f"--- File: {label} ---\n{body}\n--- End of {label} ---"


def build_content(msg, ftables=None):
    """Turn a stored message plus attachment ids into OpenAI message content."""
    text = msg.get("content") or ""
    if msg.get("role") != "user" or not msg.get("attachments"):
        return text
    docs, images = collect_attachments(msg, ftables)
    text = "\n\n".join([text] + [fmt_doc(l, b) for l, b in docs]).strip()
    return with_images(text, images)


def with_images(text, images):
    if not images:
        return text
    parts = [{"type": "text", "text": text or "See the attached image(s)."}]
    parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
    return parts


def char_len(msgs):
    """Characters of text in a message list (images don't count)."""
    n = 0
    for m in msgs:
        c = m["content"]
        n += len(c) if isinstance(c, str) else sum(len(p.get("text", "")) for p in c)
    return n


# ---------------------------------------------------------------- splitting large sends
PART_PROMPT = """My files are too large to send in one message, so I'm sending them in {n} parts. This is part {k} of {n}.

{chunk}

--- End of part {k} of {n} ---

My request: {question}

Using only this part, write notes on everything relevant to my request: key facts, findings, code details, numbers, names, and short quotes, each with the file name it came from. These notes will be combined with notes from the other parts to write the final answer, so be thorough but concise and don't answer the request yet. If nothing in this part is relevant, reply only: Nothing relevant in this part."""

MERGE_PROMPT = """Merge these notes into one set, keeping every detail and file name relevant to this request: {question}

{notes}"""

FINAL_PROMPT = """My files were too large to send at once, so they were read in {n} parts and notes were taken from each part. Here are the notes:

{notes}

--- End of notes ---

Using these notes{img}, respond to my request as if you had read the files directly. Only mention that the files were read in parts if it matters, for example if information may be missing.

My request: {question}"""


def split_text(text, size):
    """Split text into pieces of at most `size` characters, preferring line breaks."""
    out = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        out.append(text)
    return out


def pack(blocks, size):
    """Greedily join text blocks into chunks of at most `size` characters."""
    chunks, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) + 2 > size:
            chunks.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        chunks.append(cur)
    return chunks


def doc_chunks(docs, size):
    """Turn documents into chunks of at most `size`, splitting big files into labelled sections."""
    blocks = []
    for label, body in docs:
        segs = split_text(body, max(1000, size - len(label) * 2 - 200))
        for i, seg in enumerate(segs):
            l = label if len(segs) == 1 else f"{label} (section {i + 1} of {len(segs)})"
            blocks.append(fmt_doc(l, seg))
    return pack(blocks, size)


@app.route("/")
def index():
    return render_template("index.html", version=VERSION)


@app.get("/api/version")
def version():
    return jsonify(name="pgPeek", version=VERSION)


@app.get("/api/config")
def config():
    return jsonify(version=VERSION, default_model=DEFAULT_MODEL, system_prompt=SYSTEM_PROMPT, base_url=BASE_URL,
                   max_upload_mb=MAX_UPLOAD_MB, max_table_upload_mb=MAX_TABLE_UPLOAD_MB,
                   table_extensions=sorted(filesql.TABLE_EXTS) if filesql.available() else [], max_send_chars=MAX_SEND_CHARS, allowed_extensions=sorted(ALLOWED_EXTS),
                   special_names=sorted(SPECIAL_NAMES))


@app.get("/api/models")
def models():
    """List models from the endpoint; fall back to the default if unsupported."""
    try:
        ids = sorted(m.id for m in client.models.list())
        if DEFAULT_MODEL not in ids:
            ids.insert(0, DEFAULT_MODEL)
        return jsonify(models=ids)
    except Exception as e:  # many compatible servers don't implement /models
        return jsonify(models=[DEFAULT_MODEL], warning=str(e))


def ndjson(obj):
    return json.dumps(obj) + "\n"


def stream_completion(model, messages, temperature):
    stream = client.chat.completions.create(model=model, messages=messages, temperature=temperature, stream=True)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield ndjson({"delta": chunk.choices[0].delta.content})


def complete(model, messages, temperature):
    r = client.chat.completions.create(model=model, messages=messages, temperature=temperature,
                                       max_tokens=PART_NOTE_TOKENS)
    return (r.choices[0].message.content or "").strip()


def split_send(model, raw, system, temperature, ftables=None):
    """Answer a request that exceeds MAX_SEND_CHARS by reading the files in parts.

    Each part is a separate request under the limit; notes from all parts are then
    merged (in more rounds if needed) and the final answer is streamed.
    """
    sys_msgs = [{"role": "system", "content": system}] if system else []
    docs, images = [], []
    for m in raw:
        if m.get("role") == "user":
            d, i = collect_attachments(m, ftables)
            docs += d
            images += i
    conv = [{"role": m.get("role", "user"), "content": m.get("content") or ""} for m in raw]
    question = conv[-1]["content"] if conv and conv[-1]["role"] == "user" else ""
    history = conv[:-1]

    hist_budget = MAX_SEND_CHARS // 5
    while history and char_len(history) > hist_budget:      # drop oldest turns first
        history.pop(0)
    if history and history[0]["role"] == "assistant":
        history.pop(0)
    if len(question) > hist_budget:                          # huge pasted text becomes a document
        docs.append(("Pasted message", question))
        question = "Respond to my message, which is included above as 'Pasted message'."
    question = question or "Review the attached files and summarise them."

    overhead = len(system) + char_len(history) + len(question) + len(PART_PROMPT) + 100
    size = MAX_SEND_CHARS - overhead
    chunks = doc_chunks(docs, size)
    n = len(chunks)
    yield ndjson({"parts": n})

    notes = []
    for k, chunk in enumerate(chunks, 1):
        yield ndjson({"status": f"Reading part {k} of {n}…"})
        prompt = PART_PROMPT.format(n=n, k=k, chunk=chunk, question=question)
        notes.append(f"[Notes from part {k} of {n}]\n" + complete(model, sys_msgs + history + [
            {"role": "user", "content": prompt}], temperature))

    final_overhead = len(system) + char_len(history) + len(question) + len(FINAL_PROMPT) + 100
    rounds = 0
    while sum(len(x) + 2 for x in notes) > MAX_SEND_CHARS - final_overhead and len(notes) > 1:
        rounds += 1
        groups = pack(notes, MAX_SEND_CHARS - final_overhead)
        if len(groups) >= len(notes) or rounds > 5:          # can't shrink further: trim instead
            share = (MAX_SEND_CHARS - final_overhead) // len(notes)
            notes = [x[:share] for x in notes]
            break
        merged = []
        for g, group in enumerate(groups, 1):
            yield ndjson({"status": f"Combining notes ({g} of {len(groups)})…"})
            merged.append(complete(model, sys_msgs + [{"role": "user", "content": MERGE_PROMPT.format(
                question=question, notes=group)}], temperature))
        notes = merged

    yield ndjson({"status": "Writing answer…"})
    final = FINAL_PROMPT.format(n=n, notes="\n\n".join(notes), question=question,
                                img=" and the attached images" if images else "")
    yield from stream_completion(model, sys_msgs + history + [
        {"role": "user", "content": with_images(final, images)}], temperature)


# ---------------------------------------------------------------- PostgreSQL
def _err(e, code=400):
    return jsonify(error=str(e).strip().strip("'")), code


@app.get("/api/db/connections")
def db_list():
    try:
        return jsonify(connections=db.connections(), available=db.available(),
                       ssh_available=db.ssh_tunnel.available())
    except Exception as e:
        return _err(e, 500)


@app.post("/api/db/connections")
def db_create():
    data = request.get_json(force=True) or {}
    try:
        return jsonify(db.save(data, force=bool(data.get("force"))))
    except Exception as e:
        return _err(e)


@app.put("/api/db/connections/<cid>")
def db_update(cid):
    data = request.get_json(force=True) or {}
    try:
        return jsonify(db.save(data, cid=cid, force=bool(data.get("force"))))
    except KeyError as e:
        return _err(e, 404)
    except Exception as e:
        return _err(e)


@app.delete("/api/db/connections/<cid>")
def db_delete(cid):
    try:
        db.delete(cid)
        return jsonify(ok=True)
    except Exception as e:
        return _err(e)


@app.get("/api/db/connections/<cid>/status")
def db_status(cid):
    try:
        return jsonify(db.status(cid))
    except KeyError as e:
        return _err(e, 404)


@app.get("/api/fs/keys")
def browse_keys():
    """Folder listing for the SSH key-file picker (names and key types only)."""
    try:
        return jsonify(db.ssh_tunnel.browse(request.args.get("path") or None))
    except PermissionError as e:
        return _err(e, 403)
    except FileNotFoundError as e:
        return _err(e, 404)
    except Exception as e:
        return _err(e)


@app.get("/api/db/connections/<cid>/schema")
def db_schema(cid):
    try:
        return jsonify(db.schema(cid, refresh=request.args.get("refresh") == "1"))
    except KeyError as e:
        return _err(e, 404)
    except Exception as e:
        return _err(e)


@app.post("/api/db/query")
def db_query():
    data = request.get_json(force=True) or {}
    sql, cids = data.get("sql", ""), data.get("dbs") or []
    try:
        tables = file_tables(data.get("files") or [])
        target = db.resolve_target(sql, cids, has_files=bool(tables))
        if target == db.FILES_SOURCE:
            res = filesql.run_query(sql, tables, db.DB_MAX_ROWS, db.DB_TIMEOUT_MS)
        else:
            res = db.run_query(sql, cids, target)
        return jsonify(sql=sql, text=db.result_text(sql, res, db_name=res["db_name"]), **res)
    except Exception as e:
        err = str(e).strip()
        return jsonify(sql=sql, error=err, text=db.result_text(sql, error=err))


@app.post("/api/db/export")
def db_export():
    """Re-run a result card's query (read-only) and return the full result as CSV, up to DB_EXPORT_MAX_ROWS."""
    data = request.get_json(force=True) or {}
    sql, cids = data.get("sql", ""), data.get("dbs") or []
    try:
        tables = file_tables(data.get("files") or [])
        target = db.resolve_target(sql, cids, has_files=bool(tables))
        if target == db.FILES_SOURCE:
            res = filesql.run_query(sql, tables, db.DB_EXPORT_MAX_ROWS, db.DB_TIMEOUT_MS, full=True)
        else:
            res = db.run_query(sql, cids, target, max_rows=db.DB_EXPORT_MAX_ROWS, full=True)
    except Exception as e:
        return jsonify(error=str(e).strip()), 400
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(res["columns"])
    w.writerows(["" if v is None else v for v in row] for row in res["rows"])
    resp = Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8")  # BOM: Excel opens it as UTF-8
    resp.headers["X-Row-Count"] = str(res["row_count"])
    resp.headers["X-Truncated"] = "1" if res["truncated"] else "0"
    resp.headers["X-Max-Rows"] = str(db.DB_EXPORT_MAX_ROWS)
    return resp


@app.post("/api/chat")
def chat():
    """Stream a completion back as newline-delimited JSON.

    Events: {"delta": text}, {"status": text}, {"parts": n}, {"done": true}, {"error": text}.
    """
    data = request.get_json(force=True)
    raw = data.get("messages", [])
    model = data.get("model") or DEFAULT_MODEL
    temperature = float(data.get("temperature", 0.7))
    system = (data.get("system") or "").strip()

    dbs = [d for d in (data.get("dbs") or []) if isinstance(d, str)]
    tables = file_tables(chat_file_ids(raw))
    ftables = {}
    for t in tables:
        ftables.setdefault(t["fid"], []).append(t)
    if dbs or tables:
        try:
            system = (system + "\n\n" + db.system_prompt(dbs, filesql.prompt_section(tables) if tables else None)).strip()
        except Exception as e:
            system = (system + f"\n\n(Databases are enabled for this chat but unavailable: {e}. "
                      "Tell the user if they ask for data.)").strip()

    messages = [{"role": m.get("role", "user"), "content": build_content(m, ftables)} for m in raw]
    if system:
        messages = [{"role": "system", "content": system}] + messages

    def generate():
        try:
            if char_len(messages) <= MAX_SEND_CHARS:
                yield from stream_completion(model, messages, temperature)
            else:
                yield from split_send(model, raw, system, temperature, ftables)
            yield ndjson({"done": True})
        except Exception as e:
            yield ndjson({"error": f"{type(e).__name__}: {e}"})

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    # Debug mode is off by default: its in-browser debugger can run code, and its auto-reloader would
    # restart the server whenever a .zip lands in uploads/. FLASK_DEBUG=1 turns it on for development.
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    print(f" * pgPeek {VERSION}")
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 5000)), debug=debug, threaded=True,
            # watchdog matches patterns per path segment, so list each depth under uploads/
            exclude_patterns=[os.path.join(UPLOAD_DIR, *["*"] * n) for n in (1, 2, 3)] + ["*.sqlite", "*.duckdb"]
            if debug else None)
