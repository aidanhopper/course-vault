# Course Resource Vault — Technical Specification
*Version 1.0 — November 2026*

---

## 1. System Architecture Overview

The **Course Resource Vault** is a single-process, async-first Python application built with **FastAPI**, **SQLModel / SQLite**, and **yt-dlp**. It runs self-hosted and is managed with **uv**.

```
                           ┌────────────────────────┐
                           │      HTTP Client       │
                           │ (Browser, Mac App, AI) │
                           └───────────┬────────────┘
                                       │ HTTP / REST
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            FastAPI Application                              │
│                                                                             │
│  ┌───────────────────────────────────┐    ┌──────────────────────────────┐  │
│  │          /api/v1/imports          │    │      /api/v1/resources       │  │
│  └─────────────────┬─────────────────┘    └──────────────┬───────────────┘  │
│                    │                                     │                  │
│                    ▼                                     ▼                  │
│  ┌───────────────────────────────────┐    ┌──────────────────────────────┐  │
│  │            Job Runner             │    │     Byte-Range Streamer      │  │
│  │          (asyncio.Queue)          │    │     & Aspect Resolvers       │  │
│  └─────────────────┬─────────────────┘    └──────────────┬───────────────┘  │
│            │                         │                         │            │
│            ▼                         │                         │            │
│  ┌───────────────────┐               │                         │            │
│  │  Importer Router  │               │                         │            │
│  │ ┌───────────────┐ │               │                         │            │
│  │ │ YouTube Engine│ │               │                         │            │
│  │ ├───────────────┤ │               │                         │            │
│  │ │ Doc Engine    │ │               │                         │            │
│  │ └───────────────┘ │               │                         │            │
│  └─────────┬─────────┘               │                         │            │
│            │                         │                         │            │
│            ▼                         ▼                         ▼            │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                     Async SQLite Database (aiosqlite)                 │  │
│  │            [resources] [series_items] [topics] [transcripts]          │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Writes files & NFO
                                       ▼
                    ┌─────────────────────────────────────┐
                    │          Filesystem / Media         │
                    │   Videos/ {Series} / {Video}.mp4    │
                    │   Jellyfin watches this directory   │
                    └─────────────────────────────────────┘
```

---

## 2. Technology Stack & Dependencies

| Layer | Technology | Justification |
| :--- | :--- | :--- |
| **Runtime & Env** | Python 3.12+, `uv` | Modern type hints, fastest Python package & venv manager |
| **Web Framework** | `fastapi`, `uvicorn` | High performance async ASGI framework with OpenAPI docs |
| **Database & ORM** | `sqlmodel`, `aiosqlite` | Combines Pydantic models with SQLAlchemy 2.0 async engine |
| **Video Ingest** | `yt-dlp` | Python-native, battle-tested, handles chapters, subs, formats |
| **Doc Ingest** | `pypdf` | Fast, pure-Python PDF reader for page counts and outline/TOC |
| **HTTP Client** | `httpx` | Async client for downloading documents and calling LLM APIs |
| **Validation & Env**| `pydantic-settings` | Strongly-typed configuration via `.env` |
| **Media Engine** | Jellyfin (External) | Zero-sync multi-device streaming and watch state |

---

## 3. Directory Layout (`src/` Package Structure)

```text
course-platform/
├── pyproject.toml
├── .env.example
├── DESIGN.md
├── TECH_SPEC.md
├── src/
│   └── vault/
│       ├── __init__.py
│       ├── main.py              # FastAPI app, lifespan, CORS, error handlers
│       ├── config.py            # Pydantic Settings (ENV variables, media paths)
│       ├── db/
│       │   ├── __init__.py
│       │   ├── session.py       # aiosqlite engine & async sessionmaker
│       │   └── models.py        # SQLModel ORM table definitions
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── resource.py      # Pydantic discriminated union schemas
│       │   ├── topic.py         # Topic DTOs
│       │   ├── transcript.py    # Transcript cue DTOs
│       │   └── job.py           # Import job status DTOs
│       ├── api/
│       │   ├── __init__.py
│       │   ├── router.py        # /api/v1 aggregator
│       │   ├── imports.py       # POST /imports, GET /jobs/{id}
│       │   └── resources.py     # All /resources endpoints, items, and aspects
│       ├── importers/
│       │   ├── __init__.py
│       │   ├── router.py        # URL discriminator (YouTube vs PDF vs Audio)
│       │   ├── youtube.py       # yt-dlp metadata extraction, download hooks
│       │   └── document.py      # PDF parsing and TOC/outline extraction
│       ├── workers/
│       │   ├── __init__.py
│       │   └── queue.py         # In-process asyncio.Queue background worker
│       ├── ai/
│       │   ├── __init__.py
│       │   ├── cleaner.py       # LLM structured prompt for title & topic cleaning
│       │   └── fallback.py      # Heuristic regex title cleaner (offline/no API key)
│       ├── jellyfin/
│       │   ├── __init__.py
│       │   ├── nfo.py           # tvshow.nfo and episode.nfo XML builders
│       │   └── client.py        # Optional webhook / library refresh caller
│       └── media/
│           ├── __init__.py
│           └── streamer.py      # HTTP 206 Byte-Range partial content file response
└── tests/
    ├── test_api.py
    ├── test_models.py
    └── test_youtube_parser.py
```

---

## 4. Database Schema (SQLite via SQLModel)

All IDs are generated as **ULID** or **UUIDv7** strings (sortable, collision-free, URL-safe: `res_01J9...`).

### 1. `resources`
The core unified table for all assets (atomic media and composite series):

```sql
CREATE TABLE resources (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,                -- 'video', 'series', 'document', 'slides', 'audio'
    title TEXT NOT NULL,               -- Clean canonical title
    original_title TEXT,               -- Raw source title from provider
    data JSON NOT NULL DEFAULT '{}',   -- Discriminated type-specific JSON payload
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_resources_type ON resources(type);
```

### 2. `series_items`
Ordered sequence join table:

```sql
CREATE TABLE series_items (
    id TEXT PRIMARY KEY,
    parent_series_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    child_resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(parent_series_id, child_resource_id),
    UNIQUE(parent_series_id, order_index)
);
CREATE INDEX ix_series_items_parent ON series_items(parent_series_id);
```

### 3. `topics`
Knowledge segments attached to any resource:

```sql
CREATE TABLE topics (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    start_seconds REAL,                -- Video/Audio start time in seconds
    end_seconds REAL,                  -- Video/Audio end time in seconds
    start_page INTEGER,                -- Document/Slides start page/slide
    end_page INTEGER,                  -- Document/Slides end page/slide
    order_index INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_topics_resource ON topics(resource_id);
```

### 4. `transcript_cues`
High-resolution caption/spoken lines (indexed for future full-text search):

```sql
CREATE TABLE transcript_cues (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX ix_transcript_cues_resource ON transcript_cues(resource_id);
```

### 5. `source_metadata`
Immutable provenance archive:

```sql
CREATE TABLE source_metadata (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL UNIQUE REFERENCES resources(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,            -- 'youtube', 'pdf_upload', etc.
    source_url TEXT,
    source_id TEXT,                    -- YouTube video ID (e.g. 'dQw4w9WgXcQ')
    raw_payload JSON NOT NULL,         -- Complete yt-dlp info_dict
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_source_metadata_provider_id ON source_metadata(provider, source_id);
```

### 6. `jobs`
Asynchronous background import tasks:

```sql
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    series_id TEXT REFERENCES resources(id) ON DELETE SET NULL,
    status TEXT NOT NULL,              -- 'queued', 'extracting', 'cleaning', 'downloading', 'completed', 'failed'
    progress_percent REAL NOT NULL DEFAULT 0.0,
    total_items INTEGER NOT NULL DEFAULT 0,
    completed_items INTEGER NOT NULL DEFAULT 0,
    current_item TEXT,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. Background Worker & Queue Architecture

To maintain zero external service dependencies (no Redis, no Celery), the Vault uses an internal **`asyncio.Queue`** managed by FastAPI's lifespan:

```python
# Conceptual Queue Worker
class ImportJobQueue:
    def __init__(self, max_concurrent: int = 1):
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.max_concurrent = max_concurrent

    async def enqueue(self, job_id: str):
        await self.queue.put(job_id)

    async def worker_loop(self):
        while True:
            job_id = await self.queue.get()
            try:
                await self.process_job(job_id)
            except Exception as e:
                await self.mark_failed(job_id, str(e))
            finally:
                self.queue.task_done()
```

- **Thread Offloading:** `yt-dlp` operations are blocking synchronous network/disk calls. They are executed using `asyncio.to_thread(ytdl_download_fn)` so the FastAPI event loop never stutters.
- **Persistence:** Job status is written to SQLite at every state transition. If the server restarts, interrupted jobs are marked as `interrupted` or resumed safely due to idempotent file checks.

---

## 6. YouTube Importer Pipeline (`yt-dlp`)

### 1. Introspection Step (Fast Metadata Dump)
```python
ydl_opts = {
    "extract_flat": "in_playlist",
    "skip_download": True,
    "quiet": True,
    "no_warnings": True,
}
```
Extracts the playlist title, video IDs, original titles, durations, and thumbnails in ~1–3 seconds without downloading media.

### 2. AI Metadata Cleanup Step
Raw video titles are bundled into a prompt to an OpenAI-compatible endpoint (OpenAI, Ollama, Anthropic via LiteLLM):
- **Input:** Raw playlist title and list of raw video titles.
- **Output (Structured JSON):**
  - Clean series title.
  - 1-paragraph academic overview.
  - Clean episode titles (strips `"Lec 01 MIT 6.006 Fall 2011 [1080p]" -> "01. Algorithmic Thinking & Peak Finding"`).
- **Fallback:** If `OPENAI_API_KEY` is absent or the model call fails, the `fallback.py` heuristic regex cleaner strips tags (`[...]`, `(...)`, `1080p`, `HD`) and formats titles automatically.

### 3. Media Download Configuration
```python
download_opts = {
    # Preferred format: best MP4 video + best M4A audio, fallback to best standard
    "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b",
    "merge_output_format": "mp4",
    # Subtitles / Captions
    "writesubtitles": True,
    "writeautomaticsub": True,
    "subtitleslangs": ["en.*", "en"],
    "subtitlesformat": "vtt",
    # Chapters
    "embedchapters": True,
    # Thumbnails
    "writethumbnail": True,
    "postprocessors": [
        {"key": "FFmpegMetadata", "add_chapters": True},
        {"key": "FFmpegEmbedSubtitle"},
    ],
    # Progress hook
    "progress_hooks": [progress_callback],
}
```

### 4. Jellyfin Integration & File Layout
Files are written directly into the configured `MEDIA_DIR`:
```text
{MEDIA_DIR}/
  Videos/
    {Series Title}/
      tvshow.nfo
      poster.jpg
      {Series Title} - E01 - {Clean Title}.mp4
      {Series Title} - E01 - {Clean Title}.en.vtt
      {Series Title} - E01 - {Clean Title}.nfo
      {Series Title} - E01 - {Clean Title}-thumb.jpg
```
- **`tvshow.nfo`:** Formatted XML containing `<title>`, `<plot>`, and `<genre>`.
- **`{Episode}.nfo`:** Formatted XML containing `<title>`, `<episode>`, `<plot>`, and `<tag>` topic labels.

---

## 7. Byte-Range Media Streamer (`streamer.py`)

To enable native video seeking, scrubbing, and media fragments (`#t=120,300`), the `/resources/{id}/media` endpoint implements **RFC 7233 Range Requests**:

```python
async def range_stream(file_path: Path, range_header: str | None) -> Response:
    file_size = file_path.stat().st_size
    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    if not range_header:
        # Full content response
        return FileResponse(file_path, media_type=content_type)

    # Parse Range: bytes=start-end
    range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
    start = int(range_match.group(1))
    end = int(range_match.group(2)) if range_match.group(2) else file_size - 1

    chunk_size = (end - start) + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(chunk_size),
    }

    return StreamingResponse(
        read_file_chunk(file_path, start, chunk_size),
        status_code=206,
        headers=headers,
        media_type=content_type,
    )
```

---

## 8. Configuration & Environment Variables

Configured via Pydantic Settings in `src/vault/config.py`:

```ini
# Server
VAULT_HOST=0.0.0.0
VAULT_PORT=8000
VAULT_DATA_DIR=./data
MEDIA_DIR=./media

# SQLite
DATABASE_URL=sqlite+aiosqlite:///data/vault.db

# Ingest Workers
MAX_CONCURRENT_DOWNLOADS=1

# AI Cleanup (Optional)
AI_API_KEY=
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini

# Jellyfin (Optional)
JELLYFIN_URL=http://localhost:8096
JELLYFIN_API_KEY=
```

---

## 9. Phased Execution Roadmap

1. **Step 1: Scaffolding with `uv`:** Initialize project, `pyproject.toml`, and virtual environment.
2. **Step 2: Database & Models:** Implement SQLModel definitions, async engine, and migrations/initialization.
3. **Step 3: Background Worker:** Build the async job queue and job status tracker.
4. **Step 4: YouTube Ingest Engine:** Build `yt-dlp` metadata extractor, AI/fallback title cleaner, and media downloader.
5. **Step 5: File & NFO Packaging:** Structure Jellyfin flat TV directories and generate `.nfo` files.
6. **Step 6: REST API Endpoints:** Build `/api/v1/imports`, `/api/v1/resources`, and aspect resolvers (`/topics`, `/transcript`, `/media`).
7. **Step 7: PDF Document Ingest Engine:** Basic `pypdf` outline parser for textbook/slide intake.
8. **Step 8: Verification & End-to-End Tests:** Verify end-to-end import with real video/playlist.
