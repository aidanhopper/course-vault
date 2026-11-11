# Course Vault — Design Specification
*Version 2.0 — November 2026*

---

## 1. Executive Summary & Purpose

The **Course Vault** is a private, self-hosted, headless repository built specifically for **knowledge capture** of learning materials. 

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

## 3. Domain Model: Content (`Resource`) vs. Context (`Collection`)

The Vault maintains a strict separation between **digital content assets** and **contextual groupings**:

```
                       ┌─────────────────────────────┐
                       │         Collection          │
                       │    (Contextual Grouping)    │
                       │ - context: arbitrary string │
                       │   ('course', 'session'...)  │
                       └──────────────┬──────────────┘
                                      │ groups items
                                      ▼
                       ┌─────────────────────────────┐
                       │       CollectionItem        │
                       │ - resource_id               │
                       │ - context: item role        │
                       │ - order_index               │
                       └──────────────┬──────────────┘
                                      │ points to
                                      ▼
                       ┌─────────────────────────────┐
                       │          Resource           │
                       │    (Digital Content)        │
                       │ - type: video|slides|doc    │
                       └──────────────┬──────────────┘
                                      │ 1-to-many
                                      ▼
                       ┌─────────────────────────────┐
                       │            Topic            │
                       │ - start/end seconds or page │
                       └─────────────────────────────┘
```

### 1. The Resource (Pure Content Asset)

Every resource represents a physical digital asset stored once in the vault:

```json
{
  "id": "res_2afaee35985b4fac",
  "type": "video",
  "title": "Introduction to Deep Learning",
  "created_at": "2026-11-09T02:05:51Z",
  "updated_at": "2026-11-09T02:05:51Z",
  "data": {
    "duration_seconds": 3651,
    "media_url": "/api/v1/resources/res_2afaee35985b4fac/media",
    "institution": "MIT",
    "instructors": ["Sara Beery"],
    "summary": "Introduction to deep neural networks, covering fundamental building blocks.",
    "topic_tags": ["Deep Learning", "Neural Networks", "Activation Functions"],
    "has_subtitles": true,
    "has_embedded_chapters": true
  }
}
```

### Resource Types

- **`video`:** Lecture recordings, walkthroughs, conference talks (MP4, embedded chapters, VTT subtitles).
- **`slides`:** Presentation slide decks (PDF, slide count, outline topics).
- **`document`:** Problem sets, official solutions, syllabi, reading papers (PDF).
- **`audio`:** Audio-only recordings, podcasts.

---

### 2. The Collection (Contextual Grouping)

A Collection has no media files of its own. It is a contextual lens that groups resources together with arbitrary context at both the collection level and the item level:

```json
{
  "id": "col_mit67960",
  "context": "course",
  "title": "MIT 6.7960 Deep Learning",
  "data": {
    "institution": "MIT",
    "instructors": ["Phillip Isola", "Sara Beery", "Jeremy Bernstein"],
    "course_url": "https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/",
    "prerequisites": "Students should have taken 18.05 and one of 6.3720, 6.3900, or 6.C01."
  },
  "items": [
    {
      "resource_id": "res_video_01",
      "context": "lecture_video",
      "order_index": 1,
      "title": "Introduction to Deep Learning",
      "type": "video"
    },
    {
      "resource_id": "res_slides_01",
      "context": "companion_slides",
      "order_index": 1,
      "title": "Lecture 01 Slides",
      "type": "slides"
    },
    {
      "resource_id": "res_hw_01",
      "context": "problem_set",
      "order_index": 1,
      "title": "6.7960 Homework 1",
      "type": "document"
    }
  ]
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
[ YouTube Importer ]         [ Course Page Importer ]     [ PDF / Media Importer ]
 - Playlist OR Single Video   - MIT OCW / Course sites     - Direct PDF download
 - yt-dlp flat metadata       - Lecture slide PDFs         - Slide count & outline
 - Batch AI metadata cleanup  - Homework sets & solutions  - Byte-range streaming
 - Download MP4 + VTT         - Syllabus & prerequisites   - Register as document/slides
 - Embed chapters & NFO       - Pairs slides to videos
 - Register type: "video"     - Register type: "document"
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
- **Batch AI Cleanup:** Passes all raw titles and descriptions in a single batched LLM request to avoid 429 rate limits, generating clean titles, exact professors, summaries, and concept tags.
- **Media Download:** Pulls best-quality MP4, burns in chapter markers (`--embed-chapters`), extracts WebVTT captions (`.en.vtt`), and grabs thumbnails (`-thumb.jpg` and `poster.jpg`).
- **Jellyfin TV-Show Structure (Flat by default):**
  ```
  videos/
    MIT 6.006 Introduction to Algorithms/
      poster.jpg
      tvshow.nfo
      MIT 6.006 - E01 - Algorithmic Thinking.mp4
      MIT 6.006 - E01 - Algorithmic Thinking.en.vtt
      MIT 6.006 - E01 - Algorithmic Thinking.nfo
      MIT 6.006 - E01 - Algorithmic Thinking-thumb.jpg
  ```
- **Deduplication:** SQLite stores YouTube video IDs to skip existing files upon re-import or interrupted runs.

### 2. Course Page Importer (MIT OpenCourseWare)
- Accepts official course homepages (e.g. `https://ocw.mit.edu/courses/6-7960-deep-learning-fall-2024/`).
- Discovers and downloads all lecture slide PDFs, saving to `media/slides/{Series Title}/`.
- Automatically pairs each slide deck 1:1 with its corresponding video lecture in SQLite (`video.data["slides_resource_id"]`).
- Discovers and downloads all homework problem sets and official solution PDFs into `media/documents/{Series Title}/`.
- Extracts syllabus description and prerequisites into the course series manifest (`series.data["prerequisites"]`).

### 3. Document & Slides Importer (PDF)
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
- **`jobs`**: `id`, `resource_id`, `status` (`queued`, `extracting`, `cleaning`, `downloading`, `completed`, `failed`), `progress_percent`, `current_item`, `error_message`, `created_at`, `updated_at`

---

## 6. REST API Specification

### Imports & Jobs
- `POST /api/v1/imports`
  - Request: `{"url": "https://...", "resource_id": null, "title_override": null}`
  - Response: `202 Accepted` with `{"job_id": "job_...", "resource_id": "res_...", "status": "queued"}`
- `GET /api/v1/jobs`
  - List all import jobs (supports `?status=downloading|completed|failed`).
- `GET /api/v1/jobs/{job_id}`
  - Returns current job status, progress %, and active step details.
- `POST /api/v1/jobs/{job_id}/cancel`
  - Abort active import job.

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
