# Changelog

All notable changes to pgPeek. Versions follow `MAJOR.MINOR.PATCH`:

- **PATCH** (1.0.**1**) for fixes,
- **MINOR** (1.**1**.0) for new features,
- **MAJOR** (**2**.0.0) for changes that break existing setups, such as settings, saved data or the API.

The version is set in `version.py`. It's shown at the bottom of the sidebar and on the Help page, returned by `/api/version`, and recorded in chat exports.

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
