# ChatLLM

A Tkinter desktop client for MiniMax, QuickRouter, and NVIDIA NIM. Supports
streaming chat, attachments, image/music generation, and local conversation history.

## Setup

Requires Python 3.11+ with Tkinter and a graphical desktop. Windows is the primary
platform; Linux also needs `python3-tk`.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Configuration

Create a project `.env` using `.env.example` as a template. Configure only the
providers you use: both the API key and the relevant endpoint URL are required.
There are no hardcoded endpoint defaults; existing process variables take precedence.

| Provider | API key | Required endpoint variables |
| --- | --- | --- |
| MiniMax | `MINIMAX_API_KEY` | `MINIMAX_OPENAI_BASE_URL` (chat), `MINIMAX_BASE_URL` (image/music) |
| QuickRouter | `QUICKROUTER_API_KEY` | `QUICKROUTER_BASE_URL` |
| NVIDIA NIM | `NVIDIA_API_KEY` | `NVIDIA_NIM_BASE_URL` |

Keep `.env` for keys and URLs only. Timeouts, automatic titles, and stream mode
are constants at the top of `tk-ChatLLM.py`. Endpoints must use HTTPS; certificate
errors require a trusted CA configuration, not disabled verification.

## Run

```powershell
.\.venv\Scripts\python.exe -B tk-ChatLLM.py
```

- Enter sends; Shift+Enter inserts a newline. Cancel and retry are available.
- Model names and capabilities are defined once in each `providers/*_api.py`
  file's `MODELS` table; `PROVIDERS` and the UI list are generated from it.
- Attachments support UTF-8 text and model-supported images/video; PDF and audio
  input are not supported. Limits: text 1 MiB, images 10 MiB, video/total 20 MiB.
- API access and charges depend on your provider account. Automatic titles make
  an extra request; cancelling locally may not cancel remote processing or charges.

## Data

History uses one JSON file per conversation in `conversations/`, alongside
`attachments/` snapshots and `cache/` media. Changed conversations use compact,
atomic JSON writes; unchanged saves are skipped. Existing files remain compatible.
Startup scans history into summaries; message bodies load on selection, with the
eight most recently used conversations cached (active or unsaved work stays loaded).
Deleting a conversation leaves shared attachments/media intact; no automatic
cleanup is performed. Data is not encrypted: back up the whole directory and do
not commit it or `.env`. Use one app instance per data directory.