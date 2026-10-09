# Changelog

All notable changes to pgPeek. Versions follow `MAJOR.MINOR.PATCH`:

- **PATCH** (1.0.**1**) for fixes,
- **MINOR** (1.**1**.0) for new features,
- **MAJOR** (**2**.0.0) for changes that break existing setups, such as settings, saved data or the API.

The version is set in `version.py`. It's shown at the bottom of the sidebar and on the Help page, returned by `/api/version`, and recorded in chat exports.

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
