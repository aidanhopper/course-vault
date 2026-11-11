import mimetypes
import re
from pathlib import Path
from typing import AsyncGenerator
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

CHUNK_SIZE = 64 * 1024  # 64 KB


async def file_chunk_generator(
    path: Path, start: int, length: int
) -> AsyncGenerator[bytes, None]:
    """Asynchronously reads and yields chunks of a file for HTTP range streaming."""
    with open(path, "rb") as f:
        f.seek(start)
        bytes_left = length
        while bytes_left > 0:
            to_read = min(CHUNK_SIZE, bytes_left)
            chunk = f.read(to_read)
            if not chunk:
                break
            bytes_left -= len(chunk)
            yield chunk


def stream_media_file(path: Path, range_header: str | None = None) -> Response:
    """
    Serves a media file with full RFC 7233 byte-range support (206 Partial Content).
    Enables native HTML5 scrubbing, seeking, and media fragments (#t=start,end).
    """
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")

    file_size = path.stat().st_size
    content_type, _ = mimetypes.guess_type(str(path))
    content_type = content_type or "application/octet-stream"

    if not range_header:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        }
        return FileResponse(path, media_type=content_type, headers=headers)

    # Parse Range: bytes=start-end
    match = re.match(r"bytes=(\d+)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    start_str, end_str = match.groups()
    start = int(start_str)
    end = int(end_str) if end_str else file_size - 1

    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = (end - start) + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }

    return StreamingResponse(
        file_chunk_generator(path, start, content_length),
        status_code=206,
        headers=headers,
        media_type=content_type,
    )
