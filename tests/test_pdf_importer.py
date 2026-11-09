import pytest
from pypdf import PdfWriter
from vault.db.models import Resource, Topic
from vault.db.session import async_session_maker, init_db
from vault.importers.document import extract_pdf_topics
from pypdf import PdfReader
import io


def create_sample_pdf_with_bookmarks() -> bytes:
    writer = PdfWriter()
    # Add 3 blank pages
    p1 = writer.add_blank_page(width=612, height=792)  # Portrait
    p2 = writer.add_blank_page(width=612, height=792)
    p3 = writer.add_blank_page(width=612, height=792)

    # Add outline bookmarks
    writer.add_outline_item("Chapter 1: Foundations", 0)
    writer.add_outline_item("Chapter 2: Sorting", 1)
    writer.add_outline_item("Chapter 3: Trees", 2)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_pdf_topics():
    pdf_bytes = create_sample_pdf_with_bookmarks()
    reader = PdfReader(io.BytesIO(pdf_bytes))
    topics = extract_pdf_topics(reader)

    assert len(topics) == 3
    assert topics[0]["title"] == "Chapter 1: Foundations"
    assert topics[0]["start_page"] == 1
    assert topics[1]["title"] == "Chapter 2: Sorting"
    assert topics[1]["start_page"] == 2
    assert topics[2]["title"] == "Chapter 3: Trees"
    assert topics[2]["start_page"] == 3
