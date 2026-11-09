import pytest
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from vault.media.streamer import stream_media_file


def test_stream_media_file_not_found(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.mp4"
    with pytest.raises(HTTPException) as exc_info:
        stream_media_file(non_existent)
    assert exc_info.value.status_code == 404


def test_stream_media_file_full(tmp_path: Path):
    sample_file = tmp_path / "sample.txt"
    sample_file.write_bytes(b"0123456789ABCDEF")

    response = stream_media_file(sample_file, range_header=None)
    assert isinstance(response, FileResponse)
    assert response.headers["Content-Length"] == "16"
    assert response.headers["Accept-Ranges"] == "bytes"


@pytest.mark.asyncio
async def test_stream_media_file_range(tmp_path: Path):
    sample_file = tmp_path / "sample.txt"
    sample_file.write_bytes(b"0123456789ABCDEF")

    # Range: bytes=0-4 (first 5 bytes)
    response = stream_media_file(sample_file, range_header="bytes=0-4")
    assert isinstance(response, StreamingResponse)
    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 0-4/16"
    assert response.headers["Content-Length"] == "5"

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"01234"


def test_stream_media_file_invalid_range(tmp_path: Path):
    sample_file = tmp_path / "sample.txt"
    sample_file.write_bytes(b"0123456789ABCDEF")

    with pytest.raises(HTTPException) as exc_info:
        stream_media_file(sample_file, range_header="bytes=50-60")
    assert exc_info.value.status_code == 416
