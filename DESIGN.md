# Course Resource Vault — Design Specification
*Version 1.4 — November 2026*

---

## 1. Executive Summary & Purpose

The **Course Resource Vault** is a private, self-hosted, headless repository built specifically for **knowledge capture** of learning materials. 

It is **not** an LMS, and it is **not** an interactive course planner. Instead, it is an unopinionated, canonical media and text archive that ingests educational materials (YouTube playlists, single mega-videos, textbook PDFs, slide decks, audio recordings), captures their knowledge (video, audio, transcripts, timestamped/page-indexed topics), organizes them cleanly on disk for media servers like Jellyfin, and exposes them through a structured, aspect-driven API.

Downstream consumers—such as Jellyfin apps on TV/mobile, an AI tutor, or a custom Mac study workspace—read from the Vault to build their own custom study views, while the Vault remains the pristine source of truth.

---

## 2. Core Principles

1. **Own the Library:** Media and knowledge live on storage you control. Zero algorithms, recommendations, tracking, or autoplay.
2. **Narrow Jobs, Small Services:**
   - **The Vault:** Ingests, cleans metadata, extracts topics/transcripts, stores files, and serves content via API.
   - **Jellyfin:** Streams media and manages watch resume positions across TV and mobile apps.
   - **The Study Client:** Tracks personal study progress, bookmarks, flashcards, and notes.
3. **Stateless Regarding Users:** The Vault stores *resources*, not *user state*. It has no user accounts, no watch ticks, and no study streaks. Downstream clients maintain their own progress.
4. **Everything is a Resource (Composite Pattern):** 
   - An atomic piece of media is a resource (`type: "video"`, `"audio"`, `"document"`, `"slides"`).
   - An ordered lecture sequence is also a resource (**`type: "series"`**).
   - Resources exist independently and can be linked into multiple series without duplicating disk space or metadata.
5. **Canonical & Unopinionated:** The Vault captures canonical resources. Downstream clients filter or transform topics for their specific needs, but never mutate the Vault's ground truth.
6. **Immutable Raw Provenance:** The raw upstream source data (original YouTube titles, raw metadata dictionaries, unedited transcripts) is preserved permanently.
7. **Capability-Driven Resources:** Every resource shares a standard envelope with a type-specific `data` block and standard capabilities: *streamable*, *readable*, and *topic-indexed*.

---

## 3. Domain Model (Composite Pattern)

```
                       ┌─────────────────────────────┐
                       │          Resource           │
                       │   (Unified Core Entity)     │
                       └──────────────┬──────────────┘
                                      │
            ┌─────────────────────────┴─────────────────────────┐
            ▼                                                   ▼
┌─────────────────────────────┐                     ┌─────────────────────────────┐
│       Atomic Resource       │                     │      Composite Resource     │
│  - type: "video"            │                     │  - type: "series"           │
│  - type: "document"         │                     │  - defines ordered sequence │
│  - type: "slides"           │                     │    of child resources       │
│  - type: "audio"            │                     └──────────────┬──────────────┘
└──────────────┬──────────────┘                                    │
               │ 1-to-many                                         │ orders
               ▼                                                   ▼
┌─────────────────────────────┐                     ┌─────────────────────────────┐
│            Topic            │                     │         SeriesItem          │
│  - Video: start/end seconds │                     │  - parent_series_id         │
│  - Book:  start/end pages   │                     │  - child_resource_id        │
└─────────────────────────────┘                     │  - order_index (1, 2, 3...) │
                                                    └─────────────────────────────┘
```

### Base Resource Envelope

Every resource shares a standard envelope:

```json
{
  "id": "res_01J9...",
  "type": "video | series | document | slides | audio",
  "title": "Clean Canonical Title",
  "original_title": "Raw Source Title",
  "created_at": "2026-11-08T12:00:00Z",

  "data": { ... }
}
```

---

### Type-Specific `data` Schemas

#### 1. `type: "video"` (YouTube Lecture / Video)
```json
{
  "duration_seconds": 3012,
  "file_path": "Videos/MIT 6.006/MIT 6.006 - E01 - Peak Finding.mp4",
  "jellyfin_item_id": "a1b2c3...",
  "has_subtitles": true,
  "has_embedded_chapters": true
}
```

#### 2. `type: "series"` (Ordered Sequence of Videos)
```json
{
  "instructor": "Erik Demaine, Srini Devadas",
  "overview": "Clean course overview...",
  "jellyfin_series_id": "s1t2u3...",
  "items": [
    { "order_index": 1, "resource_id": "res_peak_finding" },
    { "order_index": 2, "resource_id": "res_models_of_computation" }
  ]
}
```

#### 3. `type: "document"` (Textbook / Reading)
```json
{
  "page_count": 1312,
  "file_format": "pdf",
  "file_path": "Documents/CLRS_3rd_Edition.pdf",
  "table_of_contents_depth": 3
}
```

#### 4. `type: "slides"` (Lecture Slide Deck)
```json
{
  "slide_count": 48,
  "file_format": "pdf",
  "file_path": "Slides/MIT_6006_L01_Slides.pdf"
}
```

#### 5. `type: "audio"` (Audio Recording / Podcast)
```json
{
  "duration_seconds": 1840,
  "file_path": "Audio/Discussion_01.m4a",
  "jellyfin_item_id": "d4e5f6..."
}
```

---

## 4. The Importer Architecture

The Vault uses a unified URL importer. The caller submits a URL, and the internal **Importer Router** selects the appropriate handler:

```
              POST /api/v1/imports {"url": "...", "series_id": "optional"}
                                     │
                             Importer Router
                                     │
       ┌─────────────────────────────┼─────────────────────────────┐
       ▼                             ▼                             ▼
[ YouTube Importer ]         [ Document Importer ]        [ Audio Importer ]
 - Playlist OR Single Video   - Download PDF (httpx)       - Download audio (yt-dlp/direct)
 - yt-dlp flat metadata       - Extract outline/TOC        - Extract topics / chapters
 - AI title & topic cleanup   - Detect slides vs book      - Save to vault /audio
 - Download MP4 + VTT         - Save to vault /documents   - Register type: "audio"
 - Embed chapters & NFO       - Save to vault /documents   - Register type: "document"
 - Register type: "video"
```

### 1. YouTube Importer (Playlists & Single Videos)
- **Playlists:**
  - Creates a resource of `type: "series"` for the playlist.
  - Ingests each video as a child resource of `type: "video"`.
  - Links them into `series_items` with sequential `order_index: 1, 2, 3...`.
- **Single Video / "Full Course in One Video":**
  - If imported without `series_id`: creates a `type: "series"` resource containing this 1 video.
  - The video's chapter markers are extracted directly into individual **`Topic`** records with exact start and end timestamps.
  - A study client can treat each `Topic` as a discrete modular lesson, while Jellyfin renders them as chapters on the scrubber bar.
- **Adding to an Existing Series:**
  - If a single video URL is imported with an existing `series_id`, it is downloaded once, cataloged as a `type: "video"` resource, and appended to that series sequence.
- **Metadata extraction:** Runs `yt-dlp` in flat-dump mode for fast introspection.
- **AI Cleanup:** Passes raw titles to an internal LLM to strip clickbait, numbers, and tags (`[1080p]`, `Fall 2023`) while keeping actual lecture topics intact. Falls back cleanly to regex heuristics if no AI key is configured.
- **Media Download:** Pulls best-quality MP4, burns in chapter markers (`--embed-chapters`), extracts WebVTT captions (`.en.vtt`), and grabs thumbnails (`-thumb.jpg` and `poster.jpg`).
- **Jellyfin TV-Show Structure (Flat by default):**
  ```
  Videos/
    MIT 6.006 Introduction to Algorithms/
      poster.jpg
      tvshow.nfo
      MIT 6.006 - E01 - Algorithmic Thinking.mp4
      MIT 6.006 - E01 - Algorithmic Thinking.en.vtt
      MIT 6.006 - E01 - Algorithmic Thinking.nfo
      MIT 6.006 - E01 - Algorithmic Thinking-thumb.jpg
  ```
- **Deduplication:** SQLite stores YouTube video IDs to skip existing files upon re-import or interrupted runs.

### 2. Document & Slides Importer (PDF)
- Downloads the PDF document.
- Uses `pypdf` to parse page count and outline (Table of Contents).
- Converts the outline entries into `Topic` records with `start_page` and `end_page`.
- Classifies as `slides` or `document` based on aspect ratio and metadata.

---

## 5. Storage & Database Schema

The database is built on **SQLite** with **aiosqlite / SQLModel**:

- **`resources`**: `id`, `type`, `title`, `original_title`, `data` (JSON), `created_at`
- **`series_items`**: `id`, `parent_series_id`, `child_resource_id`, `order_index` (ordered join)
- **`topics`**: `id`, `resource_id`, `title`, `start_seconds`, `end_seconds`, `start_page`, `end_page`, `order_index`
- **`transcript_cues`**: `id`, `resource_id`, `start_seconds`, `end_seconds`, `text` (ready for playback sync and future SQLite FTS5 search)
- **`source_metadata`**: `id`, `resource_id`, `provider` (`"youtube"`), `payload` (raw JSON)
- **`jobs`**: `id`, `series_id`, `status` (`queued`, `extracting`, `cleaning`, `downloading`, `completed`, `failed`), `progress_percent`, `current_item`, `error_message`, `created_at`, `updated_at`

---

## 6. REST API Specification

### Imports & Jobs
- `POST /api/v1/imports`
  - Request: `{"url": "https://...", "series_id": null, "title_override": null}`
  - Response: `202 Accepted` with `{"job_id": "job_...", "series_id": "res_...", "status": "queued"}`
- `GET /api/v1/jobs/{job_id}`
  - Returns current job status, progress %, and active step details.

### Resources (Universal Endpoints)
- `GET /api/v1/resources`
  - List resources. Supports filter: `?type=series`, `?type=video`, `?type=document`, etc.
- `POST /api/v1/resources`
  - Create a resource (e.g. create a series or document).
- `GET /api/v1/resources/{id}`
  - Returns core envelope + `data` block.
- `GET /api/v1/resources/{id}?include=topics,transcript,source_metadata,items`
  - Single-trip composite call hydrating requested aspects (including child items if series).
- `GET /api/v1/resources/{id}/topics`
  - Returns topic segments with time ranges or page ranges.
- `GET /api/v1/resources/{id}/transcript?format=json|text|vtt`
  - Returns transcript cues as JSON array, clean plain text, or raw WebVTT.
- `GET /api/v1/resources/{id}/source-metadata`
  - Returns raw unadulterated source provider dictionary (e.g. `yt-dlp` info JSON).
- `GET /api/v1/resources/{id}/media`
  - Direct HTTP byte-range stream (`206 Partial Content`) of the video, audio, or document file.
- `POST /api/v1/resources/{id}/items`
  - Add child resource to an ordered series: `{"resource_id": "res_...", "order_index": 3}`.
- `DELETE /api/v1/resources/{id}/items/{child_resource_id}`
  - Remove child resource from an ordered series (does not delete the child resource).
- `DELETE /api/v1/resources/{id}`
  - Permanently purges the resource and its underlying file from disk.

---

## 7. Out of Scope / Non-Goals

1. **Search Scraping / Catalog Search:** You paste direct URLs. Discovering or searching the YouTube catalog is out of scope.
2. **User Accounts & Study Progress:** The Vault is stateless regarding users. Watch progress lives in Jellyfin; study bookmarks and notes live in the client.
3. **Opinionated Course Planning:** The Vault does not assemble curricula, assign grades, or schedule study calendars.
4. **Mutating Upstream Metadata:** Original YouTube titles and raw metadata dictionaries are permanently retained for provenance.
