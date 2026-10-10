# Datasquint user guide

Datasquint lets you ask questions about your company's data in plain English. It works with databases your administrator has connected and with CSV and Excel files you attach yourself. You type a question, the AI writes a database query, Datasquint runs it, and the AI answers from the real numbers.

This guide covers everyday use. For installation and settings, see the README.

## Contents

- [The screen at a glance](#the-screen-at-a-glance)
- [Asking questions](#asking-questions)
- [Getting answers from data](#getting-answers-from-data)
- [Attaching files](#attaching-files)
- [Asking about CSV and Excel files](#asking-about-csv-and-excel-files)
- [Browsing tables](#browsing-tables)
- [Python scripts for later](#python-scripts-for-later)
- [Removing files and freeing space](#removing-files-and-freeing-space)
- [Saving, exporting and importing chats](#saving-exporting-and-importing-chats)
- [Database connections](#database-connections)
- [Settings](#settings)
- [Tips for better answers](#tips-for-better-answers)
- [What stays private](#what-stays-private)
- [Common questions](#common-questions)

## The screen at a glance

**Left sidebar**

| Item | What it does |
|---|---|
| **New chat** | Starts a fresh conversation. |
| Chat list | Your chats, newest first. Click one to open it; ✎ renames it, ✕ deletes it. |
| **Settings** | System prompt, temperature, and automatic running of queries. |
| **Database** | Add or edit database connections. |
| **Export all chats** / **Import chats** | Back up your chats to a file, or load them back. |
| **Storage** | See every uploaded file and delete the ones you no longer need. |
| **Delete all chats** | Clears your chat list, optionally with the files in it. |
| **Help** | Opens this guide. |
| Version | The Datasquint version, shown at the very bottom (for example *Datasquint 1.0.0*). Mention it when reporting a problem. |

The panel button at the top left of the main area hides or shows the sidebar.

**Top bar**

- **Model**: which AI model answers. Your administrator decides which ones are available.
- **Database chip**: which data sources this chat can use. It shows a database name, *2 databases*, *Files*, or *Database off*. A blue dot means the source is connected; red means there's a problem.
- **Tables**: browse the tables this chat can use, with their columns and rows. See [Browsing tables](#browsing-tables).
- **Download icon** (right): export the current chat, including as a Python script.

**Message box** (bottom)

- **Enter** sends; **Shift+Enter** starts a new line.
- The 📎 button attaches files. You can also drag files onto the window or paste images.
- While an answer is being written, the send button becomes a **■ stop** button.

## Asking questions

Type your question and press Enter. The answer appears word by word.

Under each message:

- **Copy** copies the text.
- Code in an answer (Python, SQL, CSV, scripts and so on) has **Download** and **Copy** buttons in its top-right corner. **Download** saves it as a file with the right ending (`.py`, `.csv`, `.sql`…). If the answer names the file, for example *"save this as `report.py`"*, that name is used; hover over **Download** to see it. Tip: ask *"give me this as a CSV"* or *"write a Python script for this"* to get a file you can download.
- **Edit** (your messages) opens the message for editing. Change it, then press **Ctrl+Enter** (⌘+Enter on a Mac) or **Save & send**. Press **Esc** to cancel. Saving replaces that message and everything after it; the editor tells you how many messages that is.
- **Regenerate** (the last answer) asks for a fresh answer to the same question.

Each chat remembers everything said in it, so follow-up questions like *"now split that by month"* work. Start a new chat when you change topic: answers are faster and more focused.

## Getting answers from data

When a chat has a database or data file, the AI answers data questions by writing a query, shown in a dark code box.

1. Press **Run** on the query, or turn on **Settings → Run the model's SQL queries automatically** to skip this step.
2. The result appears as a table card.
3. The AI reads the result and answers your question. If the query had an error, it fixes it and tries again.

On a result card:

- **SQL** (click to expand) shows the exact query.
- **Download CSV** saves the **complete** result as a CSV file that opens in Excel. The card shows at most 100 rows, but the download includes every row (up to 100,000) because it runs the query again.
- **Copy CSV** copies the rows shown on the card, for pasting into Excel.
- **Run again** re-runs the query, for example after the data has changed.

Queries can only **read** data. Datasquint blocks anything that would change or delete it, so you can't damage the data by asking a question.

**Choosing data sources**: click the database chip and tick the databases this chat may use. New chats start with your last choice. To check what the AI can see, ask *"What tables do you have access to?"*

At most 200 rows come back to the AI from one query, so ask for summaries (totals, averages, top 10, counts by month) rather than *"show me every order"*. If you need every row, use **Download CSV** on the result card.

## Attaching files

Click 📎, drag files anywhere onto the window, or paste an image. You can attach several at once. Each file shows as a chip above the message box:

- *Uploading 45%* while it uploads.
- *Loading table…* while a CSV or Excel file is turned into a table.
- Then its size, plus *table, 2,500 rows* for CSV and Excel files.

The **Send** button waits until all uploads finish. Click a chip's **✕** to remove it before sending (this also cancels an upload in progress).

| You can attach | Up to | The AI gets |
|---|---|---|
| CSV, TSV, Excel (.xlsx) | 200 MB each | A table it can query, plus a short preview |
| PDF, Word, text, code, JSON | 20 MB each | The text of the file |
| Images (JPG, PNG, GIF, WebP, BMP) | 20 MB each | The picture (if the model supports images) |
| Zip files | 20 MB | Every supported file inside |

Very long documents are trimmed, and very large requests are read in parts automatically. You'll see *Reading part 2 of 5…* while that happens.

## Asking about CSV and Excel files

CSV and Excel files become tables the AI can query, so totals and counts are **exact**, calculated over every row rather than estimated.

1. Attach the file and wait for *table, N rows* on its chip.
2. Ask your question, for example: *"Total quantity by board for each month"* or *"Which 10 stations had the most failures?"*
3. Press **Run** on the query (or use auto-run). The answer comes from the result.

Good to know:

- Each file, and each Excel sheet, becomes one table, named after the file: `march_orders.csv` becomes `march_orders`, and sheet *Defects* of `qa.xlsx` becomes `qa_defects`.
- **Several tables on one sheet are fine.** If tables are separated by an empty row or an empty column, each becomes its own table, and the file's chip shows how many were found (for example *4 tables*). A title line above a table, like *Weekly QA report*, names it; otherwise it's named after its first column. Leave at least one empty row or column between tables, and give each its own header row. An empty row *inside* a table is fine: rows that carry on below it stay in the same table.
- Column names become lower case with underscores: *Unit Price ($)* becomes `unit_price`.
- The first row should hold the column names.
- Files stay available for the whole chat. Attach April's file later and ask *"compare with March"*.
- If a chat also has a database, the AI can use both, querying each separately and combining the results.
- A 200 MB CSV loads in about 10 seconds. Big Excel files take longer (about a minute for 4 million rows), so for very large data, save it as CSV first.
- Tell the AI what columns mean if it isn't obvious, for example *"status D means done; times are UTC"*.

## Browsing tables

Click **Tables** in the top bar to have a look at the data before (or while) you ask about it. It lists every table this chat can use: the tables of its databases and the tables from its attached files, including files you've attached but not sent yet.

- Click a table to see its columns and a page of rows.
- Click a column heading to sort by it; click again to reverse.
- Type in **Filter rows** to keep only rows containing that text in any column.
- **Prev** / **Next** move through the rows; the bottom line shows how many there are.
- **Insert name** puts the table's name into your message, handy for *"total qty in mixed_report_station by month"*.
- **Download CSV** saves the rows you're looking at (with the filter and sort), for Excel.

Browsing only reads, and nothing you look at is sent to the AI.

## Python scripts for later

Liked an answer and want to repeat it next week without the chat? Save it as a Python script:

- **Python** on a result card saves a script for that one query.
- **Download icon → Download as Python script** saves one script with all the chat's queries.
- **Python** in the table browser saves a script that exports that table.

To use it, put the script in a folder with the original files (same names as when you attached them) and run `python script-name.py`. Each result is saved as a CSV in an `output` folder next to it. The top of the script says what to install and which files it needs. For databases it asks for the password when it runs; passwords are never saved in the script. You don't need Datasquint or the AI to run it, and someone who knows Python can change it, for example to use a different date.

## Removing files and freeing space

Uploaded files are stored on the Datasquint server until someone deletes them, and a big CSV can take 200 MB or more. There are three ways to clean up.

**Remove a file from a chat.** Hover over a file in a message you've sent and click its **✕**. You'll be asked to confirm:

- The file leaves the chat: the AI no longer sees it or can query its table, and is told it was removed. The message shows *Removed from chat: file name*.
- **Also delete it from the server** is ticked if no other chat uses the file. If another chat still uses it, the box is greyed out and the file is kept.
- Earlier answers stay as they were.

**Delete a chat.** Click **✕** next to the chat in the sidebar (or **Delete all chats**). The confirmation offers to **also delete its uploaded files** and shows how much space that frees. Files that another chat still uses are kept. Untick the box to keep the files.

**Storage panel.** Click **Storage** in the sidebar to see every file on the server:

- Its name and type, size on disk (including its table), upload date, and **Used in**: the chats that use it. Click a chat name to open that chat.
- The top line shows the total space used and how much is taken by unused files.
- **Not used by chats in this browser** in the dropdown shows only the files you can probably delete.
- 🗑 deletes one file; tick several and use **Delete selected**; or **Delete all unused** in one go.

Deleting a file that chats still use removes it from those chats too. Deleting can't be undone.

> **Shared server?** "Used in" only knows about chats in *your* browser. If colleagues use the same Datasquint, a file marked *Not used here* may be in their chats. Check before using **Delete all unused**.

## Saving, exporting and importing chats

Chats are saved automatically in your browser. Close Datasquint, come back tomorrow, click the chat and carry on.

They're tied to **this browser on this computer, at this exact address**. Opening Datasquint at a different address (for example `localhost` instead of `127.0.0.1`), in a private window, or after clearing browsing data shows an empty list. Export regularly to keep a copy.

**Export** (download icon, top right, for the current chat):

- **Download as HTML**: a page anyone can open in a browser without Datasquint. It includes the answers, result tables and attached images, and is good for sharing or printing.
- **Download as JSON**: a backup you can import later.

**Export all chats** (sidebar) saves every chat to one JSON file.

**Import chats** (sidebar) loads a JSON export. Chats you already have are skipped, so nothing is overwritten. Databases reconnect by name. Attached files only work if they're still on the Datasquint server.

## Database connections

Usually your administrator sets these up. To add one yourself: **Database → + New connection**, fill in the details, and press **Test & save**.

- **Name** is how you and the AI refer to it.
- **Notes for the model** are the most useful field. Explain what the data means: status codes, units, time zone, how your metrics are calculated, rows to ignore. For example:
  ```
  jobs.status: Q = queued, R = running, D = done
  Yield = passed / total
  Times are UTC; the workshop is in Singapore (UTC+8)
  ```
- **Connect through SSH** is for databases behind a server. **Browse…** helps pick a key file.
- Passwords are stored encrypted and never shown again; leave them blank when editing to keep them.

## Settings

| Setting | What it does |
|---|---|
| Appearance | **System** follows your computer's light or dark mode; **Light** and **Dark** fix it. |
| Colour | The colour theme: Teal, Blue, Violet, Green, Amber, Rose or Graphite. Click to preview; **Save** keeps it, **Cancel** puts the old one back. |
| System prompt | Standing instructions for every chat, such as *"Answer briefly. Use Singapore dates."* |
| Temperature | 0 gives focused, repeatable answers; higher values give more varied wording. 0.2–0.7 suits data questions. |
| Run SQL automatically | Runs the AI's queries without pressing **Run**. It stops after 5 queries in a row so a confused AI can't loop. |

Appearance and colour are remembered in this browser only, so each person (and each browser) can pick their own.

## Tips for better answers

- **Be specific**: *"Units shipped per board in Q3 2026, by month"* beats *"how are sales"*.
- **Say what you want back**: *"as a table"*, *"top 5 only"*, *"one sentence"*.
- **Explain your terms** once per chat: what *yield*, *late* or *active* means to you.
- **Check the SQL** (expand it on the result card) when a number looks surprising. The AI may have picked the wrong column or filter. Tell it, and it will fix the query.
- **One topic per chat** keeps answers fast and accurate.

## What stays private

- **On the Datasquint server**: your uploaded files, database passwords (encrypted), and the full results of every query.
- **Sent to the AI model**: your messages, attached file text or previews, table and column names with a few sample rows, and query results (at most 200 rows each).
- **In your browser**: your chats.

Only attach files and connect data you're allowed to send to the AI provider your organisation uses.

## Common questions

**My chats disappeared.** You're probably at a different address, in a private window, or browsing data was cleared. Try the address you used before, or import your last export.

**A CSV shows only its size, not "table, N rows".** It couldn't be read as a table. Check that it's a real CSV with a header row; if it is, ask your administrator: tables may be turned off on this server (`FILE_TABLES=0`), or DuckDB may not be installed. When tables are off, CSV and Excel files are limited to 20 MB and the AI reads them as text instead of querying them.

**"Over the 200 MB limit".** Split the file, or ask your administrator to raise the limit.

**The answer says it can't access the database.** Click the database chip and tick the database for this chat.

**The chip is red.** The database can't be reached right now. Hover over it for the reason, or ask your administrator.

**The AI's number looks wrong.** Expand **SQL** on the result card to see what it calculated, then tell it what to change, such as *"exclude test boards"* or *"use order_date, not ship_date"*.

**A file shows "(unavailable)".** It was deleted from the server, or the chat was imported from another Datasquint. Attach the file again.

**Storage shows "(incomplete upload)" or "still uploading…".** *Still uploading…* means a big file is still being loaded; wait for it to finish (it can't be deleted until then). *(incomplete upload)* is a leftover from an upload or delete that was interrupted; just delete it.

**A file won't delete.** Datasquint tries for a few seconds, then tells you which file is still there and why. Usually another program has it open: antivirus checking a new file, a File Explorer window, or Excel. Wait a moment or close that program, then delete it again from **Storage**.

**Where did a file I removed go?** If you ticked *Also delete it from the server*, it's gone. Otherwise it's still in **Storage**, where you can delete it later.

**Where did pgPeek go?** It's the same app under a new name, Datasquint, chosen because it now works with files as well as databases. Your chats and colour theme come across automatically when you open Datasquint at the same address as before, and exports from pgPeek import as they are.

**Which version of Datasquint is this?** It's shown at the bottom of the sidebar and at the end of this guide. The README's changelog lists what changed in each version.
