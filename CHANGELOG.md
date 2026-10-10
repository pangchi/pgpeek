# Changelog

All notable changes to Datasquint (called pgPeek up to 1.4.0). Versions follow `MAJOR.MINOR.PATCH`:

- **PATCH** (1.0.**1**) for fixes,
- **MINOR** (1.**1**.0) for new features,
- **MAJOR** (**2**.0.0) for changes that break existing setups, such as settings, saved data or the API.

The version is set in `version.py`. It's shown at the bottom of the sidebar and on the Help page, returned by `/api/version`, and recorded in chat exports.

## 2.2.0 — 2026-10-11

### Added
- **Table browser** (**Tables** in the top bar): every table the chat can use, from its databases and its attached files (including files not sent yet), with columns and types, row counts, a paged preview, sort by column, row filter, **Insert name**, **Download CSV** and **Python**. Read-only, through the same checks as queries; nothing goes to the AI.
- **Python scripts for offline use**: **Python** on each result card, **Download as Python script** in the export menu (all of a chat's queries), and **Python** in the table browser. The script rebuilds each file's tables from the original files exactly as Datasquint loaded them (same column types, sheets and split tables), connects to PostgreSQL for database queries (password from an environment variable or a prompt, never stored), runs each query and saves the results as CSV. Checked table-for-table against Datasquint on CSV, TSV, Windows-1252, multi-sheet, offset and split-table files.
- Uploads now record where each table sits in its file (Excel rows and columns, or CSV records), so scripts can rebuild split tables.
- `POST /api/tables`, `POST /api/tables/preview`, `POST /api/script`; `scriptgen.py`.

## 2.1.0 — 2026-10-10

### Added
- **Several tables in one sheet or CSV become separate tables.** Blocks separated by blank rows (stacked) or blank columns (side by side) are each loaded as their own table with their own header and column types. Before, they were merged into one misleading table: a second table's header became a data row, or side-by-side tables were glued together row by row.
  - A one-cell title line above a table (e.g. *Weekly QA report*) names it and is no longer mistaken for the header; otherwise tables are named after their first column heading.
  - Blocks that are really one table stay together: rows after a spacing blank row, a repeated header (page breaks), or a lone total line join the table above.
  - The model is told which tables came from the same sheet, and the preview labels each one.
  - Ordinary one-table files are never touched by the splitter and load exactly as before, with no extra time. A file that does hold several tables takes longer to load (about 20 s for 150 MB).
- `SPLIT_TABLES` (default `1`) and `MAX_SPLIT_TABLES` (default `30`) settings.

### Fixed
- A title row at the top of a CSV or sheet no longer becomes the column names (the real header row below it is used).

## 2.0.1 — 2026-10-10

### Changed
- **`PORT` is now in `env.example`** (with `HOST`), so the port is easy to change from 5000. Startup prints the address to open, e.g. ` * Open http://localhost:8080 in your browser`.

### Fixed
- An invalid `PORT` (not a number, or outside 1–65535) stopped with a Python traceback; it now says what's wrong.
- A port already in use now gives a clear message suggesting another `PORT`, instead of a socket error.

## 2.0.0 — 2026-10-10

### Changed
- **Renamed from pgPeek to Datasquint**, since it now works with CSV and Excel files as well as PostgreSQL. The app folder is now `datasquint`, releases are `datasquint-<version>.zip`, and exports are named `datasquint-chats-<date>.json`.

### Action needed
- Unzip into a new `datasquint` folder and copy over `.env`, `connections.sqlite`, `secret.key`, `ssh_known_hosts` and `uploads/` from the `pgpeek` folder. See [Upgrading from pgPeek](README.md#upgrading-from-pgpeek-db-helper-or-flask-chat).

### Carried over automatically
- Chats and the colour theme move from the pgPeek browser storage (`pgpeek.v1`, `pgpeek.theme`) to `datasquint.v1` and `datasquint.theme` on first load, at the same address.
- pgPeek chat exports import unchanged.

## 1.4.0 — 2026-10-10

### Added
- **`FILE_TABLES` switch in `.env`** to allow or disallow loading CSV, TSV and Excel files into DuckDB. `1` (default) keeps today's behaviour: large files up to `MAX_TABLE_UPLOAD_MB` (200 MB) that the model queries with SQL. `0` turns it off: those files are read as text like any other document, limited to `MAX_UPLOAD_MB` (20 MB). Files already loaded as tables are then sent to the model as their first lines only.
- Startup prints whether SQL over files is on, and why if it's off.

## 1.3.0 — 2026-10-09

### Added
- **Colour themes.** Settings → **Appearance**: System (follows the computer's light/dark mode), Light or Dark. **Colour**: Teal (default), Blue, Violet, Green, Amber, Rose or Graphite, each with its own sidebar tint and matching light and dark shades. Choices preview live; Save keeps them, Cancel or Esc reverts. Saved per browser in local storage (`pgpeek.theme`) and applied before the page draws, so there's no flash on load.
- The Help page follows the chosen mode and colour.

### Changed
- Your message bubbles take a light tint of the theme colour.

## 1.2.0 — 2026-10-09

### Changed
- Sidebar items now have icons: Settings (gear), Database (cylinder), Export all chats, Import chats, Storage, Delete all chats (bin) and Help.

## 1.1.1 — 2026-10-09

### Changed
- **`.env.example` is now `env.example`.** Files starting with a dot are hidden on macOS and Linux and can be left out when uploading or copying a folder, so the settings template could go missing from repositories. Setup is now `cp env.example .env`. Existing `.env` files are unaffected.

### Docs
- README notes that `.gitignore` is also a hidden file and how to check it reaches GitHub.

## 1.1.0 — 2026-10-09

### Added
- **Download button on code blocks** in replies, next to Copy (and Run for SQL). Files get the extension of the block's language: `.py`, `.csv`, `.sql`, `.js`, `.json`, `.sh`, `.ps1`, `.html` and about 50 more, `.txt` otherwise. If the reply names the file, either on the block's first line (`# file: report.py`, `-- totals.sql`) or in `code` just above it (*Save this as `summary.csv`*), that name is used; otherwise the chat title, numbered when a reply has several unnamed blocks of the same type. CSV downloads start with a UTF-8 marker so Excel shows accents correctly.
- **Download CSV on query result cards.** It re-runs the card's query (read-only, same checks) and downloads the **full** result, not just the rows shown, up to `DB_EXPORT_MAX_ROWS` (default 100,000), with long text kept in full. If the limit cuts the result off, a message says so. Works for databases and attached files.
- `POST /api/db/export`.

### Changed
- Run, Download and Copy share one toolbar in the corner of code blocks; highlighted code no longer has extra padding.

## 1.0.1 — 2026-10-09

### Fixed
- **Storage: "(incomplete upload)" left behind after Delete all unused.** Deletes used to fail silently: if a file couldn't be removed, the panel said nothing and the half-deleted folder reappeared as *(incomplete upload)*. Deletes now retry for a few seconds (Windows often holds new files open briefly for antivirus or indexing), clear read-only flags, and report anything they still can't remove with the reason and the files left.
- **Deleting an upload while it was still being processed** crashed that upload and could leave a broken folder. Uploads in progress now show in Storage as *still uploading…* and can't be deleted until they finish; **Delete all unused** skips them.
- **Empty leftover folders** (no files at all, older than a minute) are cleaned up automatically when Storage is opened.
- An upload whose folder disappears mid-way now returns a clear error instead of a server error.
- Files that are already gone count as deleted, so Storage no longer keeps showing them.
- The Storage list reloads from the server after deleting, so it always shows what's really there.
- `DELETE /api/files/<id>` returns 409 with the reason when a file can't be deleted.

## 1.0.0 — 2026-10-09

First numbered release. Earlier unnumbered builds were called *flask-chat* and then *DB Helper*.

### Chat
- ChatGPT-style web UI for any OpenAI-compatible API (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` in `.env`), with streaming replies, Stop, model picker, Markdown and code highlighting.
- Inline multi-line editing of sent messages (Ctrl+Enter to resend, Esc to cancel); the message box grows to 40% of the window.
- Chats saved in the browser; export one chat as JSON or as a standalone HTML page, export all chats as JSON, and import with duplicate detection.
- Requests over `MAX_SEND_CHARS` are read in parts and combined automatically.

### Databases
- Multiple saved PostgreSQL connections (SQLite store, passwords and keys encrypted), chosen per chat, with notes for the model.
- Strictly read-only queries: statement allow-list, single prepared statement, read-only transaction, timeout and row cap.
- SSH tunnels with password, key file (with **Browse…** picker), pasted key or agent; host-key checking.
- Run / auto-run of the model's SQL, result cards with Copy CSV and Run again, `-- db: name` routing across several sources.

### Files
- Attach images, PDF, Word, Excel, CSV, JSON, text, code and zip archives (drag, paste or paperclip), with upload progress.
- CSV, TSV and Excel files become SQL tables (DuckDB, read-only sandbox), up to `MAX_TABLE_UPLOAD_MB` (200 MB); the model gets a preview and queries the full data. `python-calamine` for fast Excel loading.
- Remove a file from a sent message, delete chats together with their files, and a **Storage** panel listing every upload with size, date and the chats using it.

### Help
- `USER_GUIDE.md` for everyday users, built into the app under **Help** (`/guide`).

### Fixes and hardening included in this release
- Flask debug mode is now off by default (`FLASK_DEBUG=1` to enable). It had exposed the in-browser debugger and restarted the server whenever a `.zip` was uploaded.
- Excel workbooks with an empty sheet no longer fail to upload.
- Re-importing your own export of a chat with query results no longer creates an "(imported)" duplicate.
- Saving chats reports when browser storage is full instead of failing silently.
