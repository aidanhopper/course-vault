# Course Resource Vault

A private, self-hosted, headless repository built for **knowledge capture** of learning materials. 

It ingests educational resources (YouTube playlists, single mega-lectures, textbook PDFs, lecture slides, audio recordings), captures their knowledge (video, audio, transcripts, timestamped/page-indexed topics), organizes them cleanly on disk for media servers like Jellyfin, and exposes them through a structured, aspect-driven API.

---

## Key Features

- **Composite Domain Model:** Everything is a `Resource`. Atomic media (`video`, `document`, `slides`, `audio`) and composite sequences (`series`).
- **Aspect-Driven API:** Query only what you need (`/topics`, `/transcript?format=json|text|vtt`, `/source-metadata`, `/media`) or hydrate in a single trip via `?include=topics,transcript,items`.
- **Native Video Streaming:** Built-in RFC 7233 byte-range support (`206 Partial Content`) for instant scrubbing and HTML5 media fragments (`#t=start,end`).
- **Jellyfin Compatibility:** Formats downloaded lectures into clean TV show directory structures with embedded chapter markers, `.en.vtt` captions, and `.nfo` metadata sidecars.
- **Robust Background Worker:** In-process async queue worker for reliable, non-blocking ingestion with deduplication and cancellation support.
- **AI & Heuristic Metadata Cleaning:** Strips YouTube noise and clickbait while preserving topics. Works 100% offline with regex fallback if no AI key is configured.

---

## Quickstart

### 1. Requirements
- Python 3.12+ (or 3.15)
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg` (installed on system for video/audio merging and chapter embedding)

### 2. Setup
```bash
# Clone and enter the repository
cd course-platform

# Install dependencies and sync virtual environment
uv sync

# Copy example environment configuration
cp .env.example .env
```

### 3. Run the Vault Server
```bash
uv run vault
# Or directly with uvicorn:
uv run uvicorn vault.main:app --host 0.0.0.0 --port 8000 --reload
```

The interactive OpenAPI Swagger documentation will be available at:
`http://localhost:8000/docs`

### 4. Run the Test Suite
```bash
uv run pytest
```

---

## API Overview

### Ingestion
- `POST /api/v1/imports` — Submit YouTube URL or PDF URL for background import:
  ```json
  {
    "url": "https://www.youtube.com/playlist?list=PLB2BE3D6041052A62",
    "series_id": null,
    "title_override": null
  }
  ```
  Returns `202 Accepted` with `job_id`.
- `GET /api/v1/jobs/{job_id}` — Check status, progress %, and current item.
- `POST /api/v1/jobs/{job_id}/cancel` — Cancel active import job.

### Resources (Unified Endpoints)
- `GET /api/v1/resources` — List resources (optional `?type=video|series|document|slides`).
- `POST /api/v1/resources` — Create a resource (e.g. create a series or document).
- `GET /api/v1/resources/{id}` — Get resource details (supports `?include=topics,transcript,source_metadata,items`).
- `GET /api/v1/resources/{id}/topics` — Get topic breakdown with start/end seconds or pages.
- `GET /api/v1/resources/{id}/transcript?format=json|text|vtt` — Get captions/transcript in JSON, plain text, or WebVTT.
- `GET /api/v1/resources/{id}/source-metadata` — Raw upstream provider metadata dictionary.
- `GET /api/v1/resources/{id}/media` — Direct byte-range stream of the video/audio/PDF file.
- `POST /api/v1/resources/{id}/items` — Link a child resource into an ordered series: `{"resource_id": "res_...", "order_index": 1}`.
- `DELETE /api/v1/resources/{id}/items/{child_id}` — Unlink a child resource from a series.
- `DELETE /api/v1/resources/{id}` — Permanently delete resource and associated media file.

---

## Architecture & Design Docs
- [`DESIGN.md`](./DESIGN.md) — Comprehensive architectural principles, domain model, and key decisions.
- [`TECH_SPEC.md`](./TECH_SPEC.md) — Deep technical specification covering database schemas, worker queues, and media streaming.
