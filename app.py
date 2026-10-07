"""
pgPeek: chat with your PostgreSQL databases and files using any OpenAI-compatible model.

Configure with environment variables (or a .env file):
    OPENAI_API_KEY   - API key
    OPENAI_BASE_URL  - e.g. https://example.com/v1
    OPENAI_MODEL     - default model name
    SYSTEM_PROMPT    - optional default system prompt
    UPLOAD_DIR       - where uploaded files are stored (default ./uploads)
    MAX_UPLOAD_MB    - per-file upload limit (default 20)
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
import json
import mimetypes
import os
import re
import shutil
import uuid
import zipfile
import posixpath

import openai

import db
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
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", 400_000))   # ~100k tokens
MAX_SEND_CHARS = int(os.getenv("MAX_SEND_CHARS", 100_000))      # per request to the API
PART_NOTE_TOKENS = int(os.getenv("PART_NOTE_TOKENS", 2000))     # max reply length for each part's notes
ZIP_MAX_FILES = int(os.getenv("ZIP_MAX_FILES", 300))
ZIP_MAX_TOTAL_MB = int(os.getenv("ZIP_MAX_TOTAL_MB", 100))
ZIP_MAX_IMAGES = int(os.getenv("ZIP_MAX_IMAGES", 10))

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
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
    raw = open(path, "rb").read()
    if b"\x00" in raw[:4096]:
        raise ValueError("This looks like a binary file, not text.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
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
    return jsonify(error=f"File is larger than the {MAX_UPLOAD_MB} MB limit."), 413


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file received."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTS and f.filename.lower() not in SPECIAL_NAMES:
        return jsonify(error=f"{ext or 'Files without an extension'} isn't an allowed file type."), 400
    fid = uuid.uuid4().hex
    name = secure_filename(f.filename) or "file"
    folder = os.path.join(UPLOAD_DIR, fid)
    os.makedirs(folder)
    path = os.path.join(folder, name)
    f.save(path)
    mime = f.mimetype if f.mimetype and f.mimetype != "application/octet-stream" else (
        mimetypes.guess_type(name)[0] or "application/octet-stream")
    meta = {"id": fid, "name": f.filename, "file": name, "mime": mime,
            "size": os.path.getsize(path), "kind": "image", "truncated": False}

    if ext in ARCHIVE_EXTS:
        try:
            text, images, stats, truncated = process_zip(path, folder)
        except Exception as e:
            shutil.rmtree(folder, ignore_errors=True)
            return jsonify(error=str(e)), 400
        if not stats["files"]:
            shutil.rmtree(folder, ignore_errors=True)
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
                shutil.rmtree(folder, ignore_errors=True)
                return jsonify(error="This BMP image couldn't be read."), 400
    else:
        try:
            text = extract_text(path, name, mime)
        except Exception as e:
            shutil.rmtree(folder, ignore_errors=True)
            return jsonify(error=str(e)), 400
        if not text.strip():
            shutil.rmtree(folder, ignore_errors=True)
            return jsonify(error="No readable text found in this file (scanned PDFs need OCR)."), 400
        meta["kind"] = "text"
        meta["truncated"] = len(text) > MAX_TEXT_CHARS
        meta["chars"] = len(text)
        with open(os.path.join(folder, "extracted.txt"), "w", encoding="utf-8") as fh:
            fh.write(text[:MAX_TEXT_CHARS])

    with open(os.path.join(folder, "meta.json"), "w") as fh:
        json.dump(meta, fh)
    return jsonify({k: meta[k] for k in ("id", "name", "mime", "size", "kind", "truncated", "stats") if k in meta})


@app.get("/files/<fid>")
def get_file(fid):
    meta = load_meta(fid)
    if not meta:
        abort(404)
    return send_from_directory(os.path.join(UPLOAD_DIR, fid), meta["file"], mimetype=meta["mime"],
                               download_name=meta["name"])


def collect_attachments(msg):
    """Return (docs, images) for a message: docs = [(label, text)], images = [data URLs]."""
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
        else:
            label = meta["name"] + (" (truncated)" if meta.get("truncated") else "")
        docs.append((label, body))
    return docs, images


def fmt_doc(label, body):
    return f"--- File: {label} ---\n{body}\n--- End of {label} ---"


def build_content(msg):
    """Turn a stored message plus attachment ids into OpenAI message content."""
    text = msg.get("content") or ""
    if msg.get("role") != "user" or not msg.get("attachments"):
        return text
    docs, images = collect_attachments(msg)
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
    return render_template("index.html")


@app.get("/api/config")
def config():
    return jsonify(default_model=DEFAULT_MODEL, system_prompt=SYSTEM_PROMPT, base_url=BASE_URL,
                   max_upload_mb=MAX_UPLOAD_MB, max_send_chars=MAX_SEND_CHARS, allowed_extensions=sorted(ALLOWED_EXTS),
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


def split_send(model, raw, system, temperature):
    """Answer a request that exceeds MAX_SEND_CHARS by reading the files in parts.

    Each part is a separate request under the limit; notes from all parts are then
    merged (in more rounds if needed) and the final answer is streamed.
    """
    sys_msgs = [{"role": "system", "content": system}] if system else []
    docs, images = [], []
    for m in raw:
        if m.get("role") == "user":
            d, i = collect_attachments(m)
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
        res = db.run_query(sql, cids)
        return jsonify(sql=sql, text=db.result_text(sql, res, db_name=res["db_name"]), **res)
    except Exception as e:
        err = str(e).strip()
        return jsonify(sql=sql, error=err, text=db.result_text(sql, error=err))


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
    if dbs:
        try:
            system = (system + "\n\n" + db.system_prompt(dbs)).strip()
        except Exception as e:
            system = (system + f"\n\n(Databases are enabled for this chat but unavailable: {e}. "
                      "Tell the user if they ask for data.)").strip()

    messages = [{"role": m.get("role", "user"), "content": build_content(m)} for m in raw]
    if system:
        messages = [{"role": "system", "content": system}] + messages

    def generate():
        try:
            if char_len(messages) <= MAX_SEND_CHARS:
                yield from stream_completion(model, messages, temperature)
            else:
                yield from split_send(model, raw, system, temperature)
            yield ndjson({"done": True})
        except Exception as e:
            yield ndjson({"error": f"{type(e).__name__}: {e}"})

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 5000)), debug=True, threaded=True)
