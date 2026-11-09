import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple
import httpx
from pypdf import PdfReader

from vault.ai.fallback import sanitize_filename
from vault.config import settings
from vault.db.models import Resource, Topic, generate_id
from vault.db.session import async_session_maker

logger = logging.getLogger(__name__)


def extract_pdf_topics(reader: PdfReader) -> List[Dict[str, Any]]:
    """Extracts outline / table of contents from a PDF and converts into Topic dicts with page ranges."""
    topics: List[Dict[str, Any]] = []
    total_pages = len(reader.pages)

    try:
        outline = reader.outline
    except Exception as e:
        logger.warning(f"Could not read PDF outline: {e}")
        outline = []

    # Flatten top-level outline items
    destinations: List[Tuple[str, int]] = []

    def parse_items(items):
        for item in items:
            if isinstance(item, list):
                parse_items(item)
            elif hasattr(item, "title") and hasattr(item, "page"):
                try:
                    page_num = reader.get_destination_page_number(item) + 1  # 1-indexed
                    destinations.append((str(item.title), page_num))
                except Exception:
                    continue

    parse_items(outline)

    # Calculate start and end page for each topic
    for idx, (title, start_page) in enumerate(destinations):
        next_page = destinations[idx + 1][1] if idx + 1 < len(destinations) else total_pages
        end_page = max(start_page, next_page - 1 if idx + 1 < len(destinations) else total_pages)
        topics.append({
            "title": title.strip(),
            "start_page": start_page,
            "end_page": end_page,
            "order_index": idx + 1,
        })

    return topics


async def import_pdf_document(
    url: str,
    title_override: str | None = None,
    series_id: str | None = None,
) -> Resource:
    """Downloads a PDF from a URL, extracts metadata and topics, and stores as a document or slides resource."""
    # 1. Download PDF to temporary location
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        pdf_bytes = resp.content

    # Determine filename and title
    url_filename = Path(url.split("?")[0]).name or "document.pdf"
    clean_stem = sanitize_filename(Path(url_filename).stem)
    final_title = title_override or clean_stem.replace("_", " ").replace("-", " ").title()

    # Read PDF
    import io
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)

    # Classify as slides or document based on first page aspect ratio (width > height * 1.25)
    is_slides = False
    if page_count > 0:
        box = reader.pages[0].mediabox
        width = float(box.width)
        height = float(box.height)
        if height > 0 and (width / height) > 1.25:
            is_slides = True

    resource_type = "slides" if is_slides else "document"
    target_dir = settings.slides_dir if is_slides else settings.documents_dir
    target_path = target_dir / f"{clean_stem}.pdf"

    # Save to disk
    with open(target_path, "wb") as f:
        f.write(pdf_bytes)

    # Extract topics from table of contents
    extracted_topics = extract_pdf_topics(reader)

    # Prepare Resource record
    resource_id = generate_id("res")
    data_payload: Dict[str, Any] = {
        "page_count": page_count,
        "file_format": "pdf",
        "file_path": str(target_path.relative_to(settings.MEDIA_DIR)),
    }
    if is_slides:
        data_payload["slide_count"] = page_count

    resource = Resource(
        id=resource_id,
        type=resource_type,
        title=final_title,
        original_title=url_filename,
    )
    resource.data = data_payload

    async with async_session_maker() as session:
        session.add(resource)
        # Add topics
        for top_dict in extracted_topics:
            topic = Topic(
                resource_id=resource_id,
                title=top_dict["title"],
                start_page=top_dict["start_page"],
                end_page=top_dict["end_page"],
                order_index=top_dict["order_index"],
            )
            session.add(topic)
        await session.commit()
        await session.refresh(resource)

    return resource
