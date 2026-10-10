# Datasquint

**Version 2.0.0** · [Changelog](CHANGELOG.md)

Ask questions about your PostgreSQL databases and your CSV and Excel files in plain language. Datasquint is a ChatGPT-style web app (Flask) that works with any OpenAI-compatible model: the model writes read-only SQL, the app runs it, and the model answers from the results. It can also read files you attach, from single documents to zipped projects.

- **Databases**: save any number of PostgreSQL connections, direct or through SSH, and choose which ones each chat can use. Queries are strictly read-only.
- **Files**: images, PDF, Word, Excel, CSV, JSON, text, code and zip archives. Attached CSV, TSV and Excel files become SQL tables, so totals and comparisons are computed exactly, not estimated.
- **Any model**: OpenAI, Azure OpenAI proxies, vLLM, Ollama, LM Studio, LiteLLM, OpenRouter and others. No tool/function-calling support needed.
- **Big inputs**: requests larger than a set size are split into parts automatically and combined into one answer.
- **Keep your chats**: chats are saved in your browser between sessions; export them as JSON backups to move or restore, or as a standalone HTML page to read, print or share.
- **Tidy storage**: remove files from a chat, delete a chat together with its files, and see and clean up everything uploaded in the **Storage** panel.

**New to Datasquint?** The [user guide](USER_GUIDE.md) explains everyday use without the technical detail. It's also built into the app: click **Help** in the sidebar, or open `/guide`. This README covers installation, configuration and how things work.

## Contents

- [Quick start](#quick-start)
- [Using databases](#using-databases)
- [Security](#security)
- [Attaching files](#attaching-files)
- [Querying CSV and Excel files](#querying-csv-and-excel-files)
- [Removing files and freeing space](#removing-files-and-freeing-space)
- [Large requests](#large-requests)
- [Saving, exporting and importing chats](#saving-exporting-and-importing-chats)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [API](#api)
- [Running in production](#running-in-production)
- [Versions and updating](#versions-and-updating)
- [Upgrading from pgPeek, DB Helper or flask-chat](#upgrading-from-pgpeek-db-helper-or-flask-chat)

## Quick start

Requires Python 3.9 or newer.

```bash
pip install -r requirements.txt
cp env.example .env        # set OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
python app.py
```

Open http://127.0.0.1:5000, then:

1. **Sidebar → Database → + New connection.** Enter the details, add a few notes about what the data means, and press **Test & save**.
2. The new database is turned on for the current chat; the chip next to the model picker shows its name.
3. Ask a question, e.g. *"How many jobs finished last week, by board?"* The model replies with a SQL block; press **Run**, and it answers from the result.

To skip pressing Run, turn on **Settings → Run the model's SQL queries automatically**.

**Colour theme**: **Settings → Appearance** picks System (follows your computer's light or dark mode), Light or Dark, and **Colour** picks one of seven colours: Teal (the default), Blue, Violet, Green, Amber, Rose or Graphite. Choices preview as you click and apply on **Save**; Cancel puts the old theme back. The theme is kept per browser (local storage key `datasquint.theme`), and the Help page follows it.

**Typing and editing messages**: Enter sends and Shift+Enter starts a new line; the message box grows to 40% of the window, then scrolls. **Edit** under any of your messages opens it in place, with its line breaks, in a box that grows to 60% of the window. There, Enter adds a new line, **Ctrl+Enter** (⌘+Enter on Mac) or **Save & send** resends it, and **Esc** or **Cancel** leaves it unchanged. Saving replaces that message and everything after it (the editor says how many messages that is), and keeps the message's attachments.

The API client is created exactly as:

```python
client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)
```

Most OpenAI-compatible servers need `/v1` at the end of `OPENAI_BASE_URL`.

## Using databases

### Adding a connection

Open **Database** in the sidebar (or **Manage connections…** in the database menu). Saved connections are listed on the left with a status dot: green is reachable, red is not (hover for the error). Each connection has:

| Field | Notes |
|---|---|
| Name | How you and the model refer to it. The model uses it in `-- db: name`. |
| Host, port, database, user, password, SSL | Standard PostgreSQL settings. |
| Schemas the model can see | Comma-separated; default `public`. |
| Notes for the model | What the data means. See [Writing good notes](#writing-good-notes). |
| Connect through SSH | Optional. See [Connecting over SSH](#connecting-over-ssh). |

**Test & save** connects first and only saves if that works. If it can't connect, **Save anyway** stores it so you can fix it later. When editing, password and key fields show *Saved – leave blank to keep*, so you can change anything else without retyping them.

`DATABASE_URL` in `.env` also works. It appears in the list as one more connection, marked `.env`, and is edited in `.env` rather than in the app.

### Connecting over SSH

Tick **Connect through SSH** and fill in the SSH host, port and user. Datasquint opens the SSH connection and forwards PostgreSQL through it itself, like `ssh -L`, so there's no separate tunnel to keep running.

- **The database host and port are as seen from the SSH server.** If PostgreSQL runs on the SSH machine, use `localhost` and `5432`. If it sits behind a bastion, use its private address, such as `10.0.1.20`.
- **Sign in with**: a password; a key file on the machine running Datasquint (not the computer with your browser), typed or picked with **Browse…**; a pasted private key; or ssh-agent / `~/.ssh` default keys. Ed25519, ECDSA and RSA keys are supported; encrypted keys need their passphrase. PuTTY `.ppk` keys must be exported to OpenSSH format first.
- **Browse…** (next to the key path) lists folders and files on the machine running Datasquint, starting in `~/.ssh`. Private keys are marked; click one to fill in the path. Public keys and PuTTY `.ppk` keys are shown but can't be picked, with a hint on what to use instead; tick **Show all files** to see everything. The picker header shows which machine and user it's browsing as.
- Each connection gets its own tunnel. Tunnels are kept alive and reopen automatically if they drop.
- Connecting reports which step failed: SSH server unreachable, SSH login, database unreachable from the SSH server, or database login.
- The SSH server must allow port forwarding (`AllowTcpForwarding yes`, which is the default).
- **Host keys**: servers in `~/.ssh/known_hosts` are trusted. A new server is trusted on first connect and its key saved to `ssh_known_hosts` in the app folder. Set `SSH_HOST_KEY_POLICY=strict` to refuse unknown servers instead. A server whose key has **changed** is always refused, because that can mean the connection is being intercepted. If the server was legitimately rebuilt, delete its line from `ssh_known_hosts`.
- With `sslmode=verify-full`, the database certificate is still checked against the host name you entered.

The `DATABASE_URL` connection can use SSH too, through the `SSH_*` settings in `.env`.

### Choosing databases for a chat

Click the database chip next to the model picker and tick the databases this chat can use. The chip shows the database name, "2 databases", or "Database off"; it turns red if a ticked database is unreachable. New chats start with your last selection, and a newly saved connection is turned on for the current chat.

For each ticked database, the model receives its notes, its schema (tables, columns, types, primary and foreign keys, approximate row counts) and instructions for writing queries. The schema is cached for 5 minutes; press **Refresh** in the Database dialog after changing tables.

### Asking questions

The model answers data questions by writing SQL in a ` ```sql ` block:

- **Run** appears on each SQL block. The result appears as a table card and is sent back to the model, which answers from it, or reads the error and fixes the query.
- **Auto-run** (Settings) runs every SQL block in a reply as soon as it finishes. It stops after 5 query rounds in a row without you typing, so a model stuck retrying can't loop forever.
- Result cards offer **Download CSV**, **Copy CSV** and **Run again**. The browser keeps up to 100 rows per result for display; the model receives up to `DB_MAX_ROWS` rows, capped at `DB_RESULT_CHARS` characters.
- **Download CSV** re-runs the card's query on the server, with the same read-only checks and timeout, and downloads the **whole** result up to `DB_EXPORT_MAX_ROWS` (default 100,000 rows), with long values kept in full. The file starts with a UTF-8 byte-order mark so Excel opens accents correctly. If the limit cuts the result off, a message says how many rows you got. Because it runs the query again, the file reflects the data at download time. **Copy CSV** copies only the rows shown on the card.
- **Code in replies**: every code block has **Download** next to **Copy**. The file gets the extension of the block's language (`.py`, `.csv`, `.sql`, `.js`, `.json`, `.sh`, `.ps1`, `.html` and many more; `.txt` if none). If the reply names the file, on the block's first line (`# file: report.py`, `// save as app.js`, `-- totals.sql`) or in `code` text just above it (*Save this as `summary.csv`:*), that name is used; otherwise it's named after the chat, numbered when a reply has several unnamed blocks of the same type. Hover over **Download** to see the name.

**Several databases in one chat**: the model starts each query with `-- db: <name>`, and the result card shows which database it ran on. A query can only use one database, so to combine data the model runs one query per database and combines the results itself. If it leaves out the line, or names a database that isn't ticked, it's told which names to use and tries again.

To check the model can see a database, ask *"What tables do you have access to?"*

### Writing good notes

The model sees table and column names, not what they mean. A few lines in **Notes for the model** prevent most wrong answers:

```
jobs.status: 'Q' = queued, 'R' = running, 'D' = done
Yield = passed_boards / total_boards
Times are stored in UTC; the workshop is in Singapore (UTC+8)
Ignore rows where is_test = true
```

Codes, units, time zones, how your metrics are calculated, and which rows to exclude help the most.

## Security

### Read-only queries

Every query passes through four independent checks in `db.py`:

1. Only queries starting with `SELECT`, `WITH`, `EXPLAIN`, `SHOW`, `VALUES` or `TABLE` are accepted.
2. Queries run as prepared statements, so PostgreSQL rejects anything containing more than one statement (`SELECT 1; DELETE ...` or `...; COMMIT; ...`) before it runs.
3. Each query runs in a `READ ONLY` transaction, on a session with `default_transaction_read_only = on`, and is always rolled back. This also blocks data-modifying CTEs and `nextval()`.
4. A statement timeout (`DB_TIMEOUT_MS`) and a row cap (`DB_MAX_ROWS`) stop runaway queries.

These were tested against PostgreSQL 16, including attempts to slip writes through. Even so, **connect with a role that can only read.** It's the one guarantee that doesn't depend on this code, and it controls which tables the model can see:

```sql
CREATE ROLE datasquint_reader LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE mydb TO datasquint_reader;
GRANT USAGE ON SCHEMA public TO datasquint_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datasquint_reader;     -- or list specific tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datasquint_reader;
ALTER ROLE datasquint_reader SET default_transaction_read_only = on;
```

### Saved passwords

Connections are stored in `connections.sqlite`. Database passwords, SSH passwords, pasted keys and passphrases are encrypted with a key in `secret.key`, which is created automatically and readable only by the account running the app. Set `APP_SECRET_KEY` to use your own key instead. Secrets are never sent back to the browser.

- **Back up `secret.key` with `connections.sqlite`.** Without the key, saved passwords can't be decrypted and must be re-entered.
- **Anyone with both files can decrypt the passwords.** Treat them like a password file. The included `.gitignore` keeps them, `.env`, `ssh_known_hosts` and `uploads/` out of git.

### What leaves your machine

- **Query results and attached files are sent to the model provider** at `OPENAI_BASE_URL`, like any other message. Only connect data you're allowed to send there, or use a self-hosted model.
- **Anyone who can open Datasquint can query your databases, read uploaded files, and delete uploads** (Storage panel or `/api/files`). It has no login. Keep `HOST=127.0.0.1` (the default), or put it behind authentication before exposing it.
- **The key file picker shows file names in the folder it may browse** (`KEY_BROWSE_ROOT`, default the app user's home). It never sends file contents to the browser, only names, sizes and a key type read from each file's first bytes. Paths outside that folder, including through `..` or symlinks, are refused. Set `KEY_BROWSE_ROOT=off` to disable it, or point it at a narrower folder such as `~/.ssh`.
- The API key stays on the server; the browser never sees it.

## Attaching files

Attach files with the paperclip, by dragging them onto the window, or by pasting images. They upload immediately and show as chips with upload progress; removing a chip cancels its upload, and Send waits until uploads finish.

| Type | Files | Sent to the model as |
|---|---|---|
| Images | JPG/JPEG, PNG, GIF, WebP, BMP | Images (needs a vision-capable model). BMP is converted to PNG. |
| Documents | PDF, Word (.docx), TXT, MD | Text. Word tables are included. Scanned PDFs need OCR first. |
| Data | CSV, TSV, Excel (.xlsx) | **SQL tables** the model queries (see [Querying CSV and Excel files](#querying-csv-and-excel-files)), plus a short preview. Without DuckDB installed: the full text, each Excel sheet as a CSV block. |
| Data | JSON, YAML, XML | Text. |
| Code | Python, JavaScript, HTML, CSS, C/C++, Java, Go, Rust and ~40 more, plus Dockerfile and Makefile | Text |
| Archives | ZIP | Each file inside, handled by the rules above |

Other types are refused in the file picker and on the server. Change the list with `ALLOWED_EXTENSIONS`, or edit `IMAGE_EXTS`, `DOC_EXTS` and `TEXT_EXTS` in `app.py`.

Files are stored under `uploads/<id>/` and chats keep only their ids, so large files don't fill the browser's storage. Excel files show each formula's last saved result, so a file generated by a script and never opened in Excel shows empty cells where formulas are.

### Zip archives

The zip is opened on the server. The model receives a listing of included and skipped files (with reasons), then each file's text under an `=== path/in/zip ===` heading, plus up to `ZIP_MAX_IMAGES` images.

- **Ignored**: `__MACOSX`, `.DS_Store` and `._*` files, and `.git`, `node_modules`, `__pycache__`, `venv`/`.venv`, `.idea` and `.vscode` folders.
- **Skipped and listed**: disallowed types, zips inside the zip, files over `MAX_UPLOAD_MB`, and anything past the file, size or image limits.
- **Rejected**: corrupt or password-protected zips, and zips with no allowed files.
- **Text limit**: `MAX_TEXT_CHARS` is shared across the whole zip. Small files are kept whole and the rest is split evenly among the larger ones, so only the largest are trimmed. Trimmed files are marked and end with a `[... truncated: showing X of Y characters]` note.
- **Safe extraction**: files are never written to their paths from inside the zip, so `../` entries can't escape, and the total-size cap stops zip bombs.

## Querying CSV and Excel files

Drop one or more CSV, TSV or Excel files into a chat (or pick several with the paperclip) and ask about them. Each file, and each sheet of an Excel file, becomes a table the model can query with SQL, the same way it queries your databases: it writes a query, you press **Run** (or auto-run does), Datasquint computes the result and the model answers from it. Totals, counts, averages, rankings and joins across files are computed exactly over every row, instead of the model estimating from text. Requires `duckdb`.

**What happens on upload**

- The file is loaded into a small DuckDB database stored with the upload (`uploads/<id>/tables.duckdb`). Column types (whole numbers, decimals, dates, timestamps, text) are detected from all rows, and the delimiter and header row are detected automatically.
- Column names are converted to `lower_snake_case`: `Unit Price ($)` becomes `unit_price`, `Order Date` becomes `order_date`. Names that are SQL keywords get a leading underscore (`Operator` becomes `_operator`); the schema the model sees shows the final names.
- **Big files**: CSV, TSV and Excel files can be up to `MAX_TABLE_UPLOAD_MB` (default **200 MB**), well above the 20 MB limit for other files, because only a preview reaches the model. The chip shows upload progress, then *Loading table…* while the table is built. Only the first 64 KB of the file is read as text.
- CSVs saved by Excel in Windows encoding (accents like *München*) are converted to UTF-8 first.
- The chip shows the result, e.g. *85 KB · table, 2,500 rows* or *2 tables, 4 rows* for a two-sheet workbook. Empty sheets are skipped.

**What the model sees**

- Instead of the whole file, the message carries a 15-line preview and a note that the full data is in a table, so a big CSV no longer fills the request or triggers [splitting](#large-requests).
- The system prompt lists each table: its name, source file and sheet, row count, column names and types, and 3 sample rows.
- **Table names** come from the file name: `march_orders.csv` → `march_orders`; Excel sheets add the sheet name (`qa.xlsx` sheets *Defects* and *Targets* → `qa_defects`, `qa_targets`). A second file with the same name becomes `march_orders_2`.
- **Every file attached anywhere in the chat** is available to every later query, so you can attach more files mid-conversation and join them with earlier ones.
- The SQL dialect is DuckDB's, which is close to PostgreSQL's: `::` casts, `ILIKE`, `date_trunc`, `string_agg`, `FILTER`, window functions and CTEs all work.

**Files and databases in one chat**: the files appear as a data source named `files` next to any ticked databases, and the chip shows e.g. *workshop + files* (or just *Files*). With more than one source, the model starts each query with `-- db: files` or `-- db: <database>`. A query can't join a file to a database table directly; the model runs one query on each and combines the results. Because of this, `files` can't be used as a saved connection name.

**Safety**: each query runs in a fresh in-memory DuckDB with the chat's files attached read-only. Only a single `SELECT` (or `EXPLAIN`) is accepted, and before your query runs the session is locked so SQL can't read or write other files, attach databases, install extensions, or change those settings back. The same row cap (`DB_MAX_ROWS`) and timeout (`DB_TIMEOUT_MS`) as database queries apply, and memory is capped at `FILES_SQL_MEMORY` (default 1 GB). Tested: `DELETE`, `COPY … TO`, `ATTACH`, `INSTALL`, `read_csv('/etc/passwd')`, `SET enable_external_access=true` and multiple statements are all refused.

**Speed and memory** (measured on a 2-core, 8 GB test machine):

| File | Rows | Load time | Server memory peak | Stored table | Query (grouped total over all rows) |
|---|---|---|---|---|---|
| 199 MB CSV | 3.1 million | 8 s | ~460 MB | 25 MB | 0.06 s |
| 194 MB Excel, 4 sheets | 4.2 million | 66 s | ~1.5 GB | 35 MB | 0.03 s per sheet |

Excel is slower because each sheet is converted to CSV first. With `python-calamine` installed (the default reader) this runs at about 65,000 rows a second and uses roughly 1 GB of memory per million-row sheet, released between sheets. Without it, or with `EXCEL_READER=openpyxl`, memory stays around 150 MB but loading is 10–40× slower, which is impractical above a few hundred thousand rows. On a machine with little memory, saving big workbooks as CSV first is the fastest and leanest option.

Disk use per file is the original (kept so it can be opened from the chat) plus the table, which is usually much smaller than the source.

**Turning it off**: set `FILE_TABLES=0` in `.env` and restart to stop CSV/TSV/Excel being loaded into DuckDB (and so disallow large ones); see [Configuration](#configuration).

**Limits**: CSV/TSV/Excel up to `MAX_TABLE_UPLOAD_MB` (200 MB), other files up to `MAX_UPLOAD_MB` (20 MB). A CSV that can't be read as a table falls back to text, and then the 20 MB limit applies. Excel allows about a million rows per sheet; bigger data needs several sheets or a CSV. CSV and Excel files inside a zip are sent as text, not tables; attach them directly to query them. Excel formulas give their last saved values (see [Attaching files](#attaching-files)).

## Removing files and freeing space

Uploads are kept on the server in `uploads/<id>/` (the original file, plus `tables.duckdb` for CSV/Excel) until something deletes them. A 200 MB CSV takes about 225 MB. There are three ways to remove them, and every one asks for confirmation first.

**Remove a file from a sent message**: hover over the file in your message and click its **✕**.

- The file is taken out of the chat. It's no longer sent with the conversation, its table disappears from the `files` source (other tables keep their names), and the message shows *Removed from chat: name*.
- The model is told, in that message, that the attachment was removed, so it doesn't keep referring to it. Earlier answers and result cards stay.
- **Also delete it from the server** is ticked when no other chat in this browser uses the file, and shows the space it frees. If another chat uses it, the option is greyed out and names that chat.
- Removing an attachment is saved with the chat, survives export/import, and is noted in the HTML export.

**Delete a chat** (✕ in the sidebar) or **Delete all chats**: the confirmation offers **Also delete its uploaded files from the server**, with their total size. Files still used by other chats in this browser are listed as kept and aren't deleted. Untick it to keep the files.

**Storage panel** (sidebar → **Storage**): every upload on the server with its name and type (table rows, zip contents), original size, size on disk, upload date, and the chats in this browser that use it. Click a chat to open it.

- The summary shows the total on disk and how much is used by files no chat here refers to; the filter shows only those.
- Delete one file (🗑), tick several and **Delete selected**, or **Delete all unused**.
- Deleting a file that chats here still use also removes it from those chats (as above).
- Uploads still being processed (a big CSV or Excel file loading) show as *still uploading…* and can't be selected or deleted until they finish; **Delete all unused** skips them.
- Folders left by an upload that never finished, or by a delete that couldn't remove everything, show as *(incomplete upload)* so they can be deleted. Completely empty ones older than a minute are removed automatically when Storage opens.
- **If a delete fails**, Datasquint retries for a few seconds and then says which file wasn't deleted, what's still there, and why. On Windows this is usually another program holding a file open: antivirus scanning a new file, search indexing, a File Explorer window in that folder, or the file open in Excel. Close it, or wait a moment, and delete again. Nothing is silently left behind.

**What "used" means**: chats live in each browser, so Datasquint only knows about the chats in the browser you're using. On a shared server, a file marked *Not used here* may be in someone else's chats, or in an exported chat file. Imported chats whose files were deleted show them as unavailable, and the model is told the file is missing. The HTML export embeds images, so it doesn't depend on files staying on the server.

Deleting is permanent. There's no recycle bin.

## Large requests

No single request to the model is larger than `MAX_SEND_CHARS` (default 100,000 characters, counting the system prompt, history, message and file text; images don't count). When a message would be bigger:

1. The file text is split into parts that fit. Files are kept together where possible; large files are cut at line breaks into labelled sections.
2. Each part is sent as its own request with your question, and the model writes notes on what's relevant.
3. A final request combines the notes, merging them in extra rounds if needed, and the answer streams back.

The reply shows progress ("Reading part 2 of 5…") and afterwards "Read in N parts".

Trade-offs: an N-part send costs N + 1 or more requests; the answer works from notes, not the full text, so it suits summaries, reviews and search-style questions better than exact rewrites; follow-up questions re-read the files each time; and only the most recent turns of history are kept in split mode. Raise `MAX_SEND_CHARS` toward your model's context window (roughly 3–4 characters per token) to send more in one go.

## Saving, exporting and importing chats

### Where chats are kept

Every chat is saved automatically in your browser's local storage as you go. Close Datasquint, come back the next day, pick the chat in the sidebar and keep typing: the model gets the whole earlier conversation, and the chat keeps its ticked databases and attachments.

Chats stay in the browser that created them, at that exact address. `http://localhost:5000`, `http://127.0.0.1:5000` and `http://192.168.1.20:5000` each count as a separate site with separate chats. Private/incognito windows, "clear site data when closing" settings and browser cleaners remove them. Use export to keep a copy that doesn't depend on any of this.

### Export

| Where | Option | What you get |
|---|---|---|
| Sidebar | **Export all chats** | `datasquint-chats-<date>.json` with every chat. A backup you can import later, here or in another browser or PC. |
| Download icon, top right | **Download as JSON** | The current chat only, in the same format. |
| Download icon, top right | **Download as HTML** | The current chat as one self-contained web page: formatted replies, SQL result tables, attached images embedded, the databases used, and the export date. Opens in any browser without Datasquint, prints cleanly, follows light/dark mode, and contains no scripts. |

The export icon is greyed out until the current chat has messages, and exporting waits until a reply has finished.

### Import

**Import chats** in the sidebar loads a JSON file made by either export (or a bare list of chats).

- Imported chats go to the top of the sidebar, and the first one opens.
- A chat that's already here with identical messages is skipped; one with the same id but different messages is added as a copy titled "… (imported)", so nothing is overwritten.
- **Databases are reconnected by name.** The export records each chat's database names, so on another PC they're matched to saved connections with the same name (case-insensitive). Chats whose databases aren't found just start with no database ticked.
- **Attachments are references to files in `uploads/` on the server**, not copies. Imported into the same Datasquint they work as before; on a different server they show as unavailable and the model is told the file is missing. The HTML export is the way to keep attached images with a chat.
- Files are checked before anything is saved: invalid JSON, files with no chats, unknown fields and malformed attachment ids are rejected or dropped. Message content is displayed as text or sanitised markdown, exactly like normal chats.
- If the browser's storage is full, the import is undone and a message says so. Browsers allow roughly 5–10 MB per site; export and delete old chats to make room.

## Configuration

Settings go in `.env` (see `env.example`). Restart `python app.py` after changing them.

**Model**

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | `your_api_key` | API key for the endpoint |
| `OPENAI_BASE_URL` | `https://example.com` | Usually ends in `/v1` |
| `OPENAI_MODEL` | `gpt-4o-mini` | Default model; others come from the endpoint's `/models` list |
| `SYSTEM_PROMPT` | `You are a helpful assistant.` | Starting system prompt; editable in Settings |

**Databases**

| Variable | Default | Notes |
|---|---|---|
| `CONFIG_DB` | `./connections.sqlite` | Where saved connections are stored |
| `APP_SECRET_KEY` | generated into `./secret.key` | Fernet key that encrypts saved passwords |
| `SECRET_KEY_FILE` | `./secret.key` | Where the generated key is kept when `APP_SECRET_KEY` isn't set |
| `DATABASE_URL` | none | Optional connection defined in `.env` |
| `DATABASE_LABEL` / `DATABASE_CONTEXT` | none | Name and notes for the `DATABASE_URL` connection |
| `DB_SCHEMAS` | `public` | Default schemas the model sees; each connection can override |
| `DB_MAX_ROWS` | `200` | Rows returned per query |
| `DB_EXPORT_MAX_ROWS` | `100000` | Most rows **Download CSV** on a result card saves |
| `DB_TIMEOUT_MS` | `15000` | Per-query timeout |
| `DB_SCHEMA_CHARS` | `20000` | Max schema size sent per database |
| `DB_RESULT_CHARS` | `20000` | Max size of each result sent to the model |

**SSH** (for the `DATABASE_URL` connection; saved connections set SSH in the app)

| Variable | Default | Notes |
|---|---|---|
| `SSH_HOST` / `SSH_PORT` / `SSH_USER` | none / `22` / none | SSH server to tunnel through |
| `SSH_PASSWORD` or `SSH_KEY_FILE` (+ `SSH_KEY_PASSPHRASE`) | none | Sign-in; with neither, uses ssh-agent and `~/.ssh` default keys |
| `SSH_HOST_KEY_POLICY` | `accept-new` | Applies to all connections. `strict` refuses unknown servers. |
| `SSH_KNOWN_HOSTS` | `./ssh_known_hosts` | Where newly trusted host keys are saved |
| `KEY_BROWSE_ROOT` | `~` (app user's home) | Folder the **Browse…** key picker may list; `off` disables it |

**Files and request size**

| Variable | Default | Notes |
|---|---|---|
| `MAX_UPLOAD_MB` | `20` | Per-file upload limit |
| `FILE_TABLES` | `1` | `1` loads CSV/TSV/Excel into DuckDB as SQL tables, allowing large files up to `MAX_TABLE_UPLOAD_MB`. `0` turns this off: those files are read as text like other documents, limited to `MAX_UPLOAD_MB`, and the model can't query them with SQL. Files loaded as tables earlier then reach the model as their first lines only. Startup prints which is in effect. |
| `MAX_TABLE_UPLOAD_MB` | `200` | Per-file limit for CSV/TSV/Excel loaded as SQL tables (needs `duckdb`; otherwise `MAX_UPLOAD_MB` applies) |
| `EXCEL_READER` | `calamine` | `calamine` (fast, ~1 GB per million-row sheet) or `openpyxl` (slow, low memory) |
| `MAX_TEXT_CHARS` | `400000` | Text kept per file or zip |
| `ALLOWED_EXTENSIONS` | built-in list | Comma list that replaces the allowed types, e.g. `.png,.pdf,.csv` |
| `ZIP_MAX_FILES` / `ZIP_MAX_TOTAL_MB` / `ZIP_MAX_IMAGES` | `300` / `100` / `10` | Zip limits |
| `FILES_SQL_MEMORY` | `1GB` | Memory limit for SQL over attached CSV/Excel files |
| `MAX_SEND_CHARS` | `100000` | Largest single request to the model |
| `PART_NOTE_TOKENS` | `2000` | Length of each part's notes when splitting |
| `UPLOAD_DIR` | `./uploads` | Where uploads are stored |

**Server**

| Variable | Default | Notes |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `5000` | `HOST=0.0.0.0` opens it to your network; see [Security](#security) |
| `FLASK_DEBUG` | `0` | `1` turns on Flask's debug mode (auto-reload on code changes, in-browser debugger) for development only. Never combine it with `HOST=0.0.0.0`: the debugger can run code. |

### Dependencies

`flask`, `openai` and `python-dotenv` are required. The rest each enable one feature, and the app explains what to install if one is missing: `psycopg[binary]` (PostgreSQL), `duckdb` (SQL over CSV/Excel files), `python-calamine` (fast Excel loading), `cryptography` (saved connections), `paramiko` (SSH), `pypdf` (PDF), `python-docx` (Word), `openpyxl` (Excel), `Pillow` (BMP).

## Troubleshooting

| Message | What to do |
|---|---|
| *Request failed … context length* | The request is larger than the model accepts. Lower `MAX_SEND_CHARS`, or start a new chat. |
| *SSH key file not found* | The path must exist on the machine running Datasquint, readable by the account it runs as. Use **Browse…** to pick it, or **Paste private key**. |
| *Browsing is limited to …* | The key is outside `KEY_BROWSE_ROOT`. Type its path instead, or widen `KEY_BROWSE_ROOT`. |
| *Connected to SSH, but the SSH server couldn't reach the database* | The database host/port are wrong as seen from the SSH server (often `localhost:5432`), or port forwarding is disabled. |
| *SSH host key … has CHANGED* | If the server was rebuilt, delete its line from `ssh_known_hosts`. Otherwise, don't connect. |
| *Can't decrypt saved connection* | `secret.key` or `APP_SECRET_KEY` changed. Restore the original key, or edit the connection and re-enter its passwords. |
| *… wasn't deleted … being used by another process* | Another program has the file open (antivirus, search indexing, File Explorer, Excel). Wait a moment or close it, then delete again from **Storage**. |
| *(incomplete upload)* in Storage | A leftover from an interrupted upload or a delete that couldn't finish. Delete it; if that fails, the message says which file is still in use. |
| Disk filling up with uploads | Open **Storage** and use **Delete all unused**, or delete old chats with their files. See [Removing files and freeing space](#removing-files-and-freeing-space). |
| Chats missing after reopening Datasquint | Check the address matches the one you used before (`localhost` vs `127.0.0.1` vs an IP), and that you're not in a private window. Restore from an exported JSON file with **Import chats**. |
| *Couldn't save chats: the browser's storage is full* | Export your chats, then delete old ones. |
| Chip is red / "Database off" | Click it to tick databases or check which one is unreachable. |
| The model says it has no database access | The database isn't ticked for this chat. |
| *Over the 200 MB limit…* / *larger than the upload limit* | Raise `MAX_TABLE_UPLOAD_MB` (CSV/Excel) or `MAX_UPLOAD_MB` (other files). Behind nginx, also raise `client_max_body_size`. |
| A big Excel file takes minutes or the server runs out of memory | Install `python-calamine` for speed; on low-memory machines use `EXCEL_READER=openpyxl`, or save the workbook as CSV first (much faster to load). |
| A CSV shows as text, not *table, N rows* | Install `duckdb` and re-attach the file; tables are made at upload. Files the parser can't read as a table still work as text. |
| *This chat has several data sources…* | Sent to the model automatically when it forgets `-- db: files` or `-- db: <database>`; it retries. |
| Formatting shows as plain text | The markdown library couldn't load from cdnjs (offline). SQL blocks, Run and auto-run still work. |

## Project layout

```
app.py               Flask app: chat streaming, uploads, splitting, API routes
db.py                PostgreSQL: connections, schema, read-only queries, prompts
store.py             Saved connections in SQLite, secrets encrypted
ssh_tunnel.py        SSH tunnels (port forwarding) to reach databases
filesql.py           SQL over attached CSV/TSV/Excel files (DuckDB, read-only sandbox)
templates/index.html The whole web UI (HTML, CSS and JS in one file)
USER_GUIDE.md        Guide for everyday users; served in the app at /guide (Help link)
CHANGELOG.md         What changed in each version
version.py           The version number (single place to change it)
requirements.txt
env.example          Settings template: copy it to .env and fill it in
.gitignore           Keeps .env, keys, the connection store and uploads out of git
```

`.gitignore` starts with a dot, so macOS and Linux hide it and it can be left out when you upload or copy the folder (for example dragging it into GitHub's *Upload files* page). Push with git (`git add .` then `git status` should list `.gitignore` and must not list `.env`), or add `.gitignore` on GitHub by hand.

Created when the app runs: `connections.sqlite` and `secret.key` (on first saved connection), `ssh_known_hosts` (on first SSH connection) and `uploads/` (on first upload).

## API

All responses are JSON unless noted.

**Chat**

- `POST /api/chat` — body `{messages, model, temperature, system, dbs}`. User messages may include `attachments: [{id}]`; `dbs` is a list of connection ids whose notes, schemas and query instructions are added to the system prompt. Returns `application/x-ndjson`: `{"delta": text}` lines, plus `{"parts": n}` and `{"status": text}` when splitting, then `{"done": true}` or `{"error": text}`.
- `GET /api/models` — `{models}`
- `GET /api/config` — default model, system prompt, base URL, upload limit, allowed extensions

**Files**

- `POST /api/upload` — multipart field `file`; returns `{id, name, mime, size, kind, truncated, stats, tables}` (`kind` is `image`, `text` or `zip`; `tables` lists `{rows, columns, sheet}` for CSV/TSV/Excel files loaded as SQL tables), or `{error}` with status 400/413
- `GET /files/<id>` — the original file
- `GET /api/files` — every upload: `{files: [{id, name, kind, size, disk, uploaded, tables, stats}], total_disk}`; `disk` includes the table; unfinished uploads have `incomplete: true`
- `DELETE /api/files/<id>` — delete one upload (404 if it's gone, 409 with `{error}` if it can't be deleted); `POST /api/files/delete` with `{ids}` deletes several → `{deleted, failed: [{id, error}], freed}` (bytes). Uploads still in progress (`processing: true` in the list) are refused; ids already gone count as deleted

**Help and version**

- `GET /api/version` — `{name, version}`; `/api/config` also includes `version`
- `GET /guide` — the user guide as a web page; `GET /guide.md` — its Markdown source

**Databases**

- `GET /api/db/connections` — saved connections, without secrets
- `POST /api/db/connections` — create; `PUT /api/db/connections/<id>` — update (blank secrets keep the saved ones); `DELETE /api/db/connections/<id>`. Body: `{name, host, port, dbname, user, password, sslmode, schemas, context, ssh_enabled, ssh_host, ssh_port, ssh_user, ssh_auth, ssh_password, ssh_key_path, ssh_key_text, ssh_passphrase, force}`, where `ssh_auth` is `password`, `key_path`, `key_text` or `agent`, and `force: true` saves without testing.
- `GET /api/db/connections/<id>/status` — tests the connection
- `GET /api/db/connections/<id>/schema[?refresh=1]` — `{text, tables}`
- `GET /api/fs/keys[?path=]` — one folder for the key picker: `{path, root, parent, dirs: [{name, path}], files: [{name, path, size, kind}], user, host}`, where `kind` is `private`, `public`, `ppk`, `other` or `unreadable`. 403 outside `KEY_BROWSE_ROOT` or when it's `off`.
- `POST /api/db/export` — same body as `/api/db/query`; returns the full result as `text/csv` (UTF-8 with BOM), up to `DB_EXPORT_MAX_ROWS`, with headers `X-Row-Count`, `X-Truncated` (`1` if cut off) and `X-Max-Rows`; `{error}` with 400 on failure
- `POST /api/db/query` — `{sql, dbs, files}` (`files`: the chat's attachment ids in message order; CSV/Excel ones become the `files` source) → `{db_id, db_name, columns, rows, row_count, truncated, ms, text}` or `{error, text}`; `text` is what the model receives

## Running in production

- Run behind gunicorn with threaded workers so streaming isn't buffered, and a timeout long enough to load big Excel files: `gunicorn -k gthread --threads 8 --timeout 300 -b 127.0.0.1:5000 app:app` (gunicorn's default 30 s timeout would cut off a 200 MB workbook). Behind nginx, turn off proxy buffering for `/api/chat`, and raise the upload size and timeout for `/api/upload`: `client_max_body_size 210m; proxy_read_timeout 300s;` (nginx's default upload limit is 1 MB).
- Add authentication in front of it before opening it beyond your own machine; see [Security](#security).
- Keep `FLASK_DEBUG` unset or `0` (the default). Earlier versions always ran in debug mode, which exposed an in-browser debugger on errors and restarted the server whenever a `.zip` was uploaded.
- Uploaded files are never deleted automatically. Use the **Storage** panel (or delete chats with their files) to clean up; see [Removing files and freeing space](#removing-files-and-freeing-space). Deleting folders in `uploads/` by hand also works; chats that refer to them show the files as unavailable.
- Chats are stored in each browser's local storage, not on the server. Clearing site data removes them; use **Export all chats** for backups. See [Saving, exporting and importing chats](#saving-exporting-and-importing-chats).

## Versions and updating

Datasquint uses `MAJOR.MINOR.PATCH` version numbers: PATCH for fixes, MINOR for new features, MAJOR for changes that break existing setups (settings, saved data or the API). The number lives in `version.py`, and [CHANGELOG.md](CHANGELOG.md) lists what each version changed.

**Which version am I running?** It's at the bottom of the sidebar and on the Help page, printed when the server starts (` * Datasquint <version>`), and returned by `/api/version`. Chat exports record the version that made them (`app_version` in JSON, the header line in HTML).

**Updating**:

1. Check [CHANGELOG.md](CHANGELOG.md) for anything marked as needing action.
2. Stop Datasquint, then unzip the new `datasquint-<version>.zip` over your `datasquint` folder. Your `.env`, `connections.sqlite`, `secret.key`, `ssh_known_hosts` and `uploads/` aren't in the zip, so they're kept.
3. Run `pip install -r requirements.txt` in case dependencies changed.
4. Start Datasquint again, and reload the page in the browser. A running server keeps serving the old page until it's restarted.

## Upgrading from pgPeek, DB Helper or flask-chat

Datasquint was called pgPeek until version 1.4.0, before that DB Helper, and before that flask-chat. It was renamed because it now works with files as well as PostgreSQL.

**From pgPeek** (nothing is lost, but the folder name changes):

1. Stop pgPeek.
2. Unzip `datasquint-<version>.zip` next to your `pgpeek` folder. It creates a new `datasquint` folder.
3. Copy these from `pgpeek` into `datasquint`: `.env`, `connections.sqlite` and `secret.key` (both, or saved passwords can't be decrypted), `ssh_known_hosts` if you use SSH, and the `uploads` folder so attached files keep working.
4. Run `python app.py` from the `datasquint` folder and open the same address as before (for example `http://127.0.0.1:5000`).

Chats and the colour theme move to the new name automatically the first time you open Datasquint in the same browser at the same address. Exports from pgPeek import as they are. Once everything looks right, delete the `pgpeek` folder. If you created a `pgpeek_reader` role from the SQL above, it keeps working; the new name in that example is only a suggestion.

**From DB Helper or flask-chat**:

- **Chats carry over automatically.** They're moved to the new storage name the first time you open Datasquint in the same browser at the same address.
- **Saved connections carry over** if you copy `connections.sqlite` and `secret.key` from the DB Helper folder into the `datasquint` folder. Copy both, or the saved passwords can't be decrypted.
- **Re-add database connections made in flask-chat's dialog.** That version only kept them in memory. `DATABASE_URL` connections keep working unchanged.
- Copy your `.env` (and `ssh_known_hosts`, if you use SSH) into the new folder.
