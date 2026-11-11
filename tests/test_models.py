import pytest
from sqlmodel import select
from vault.db.models import (
    Collection,
    CollectionItem,
    Job,
    Resource,
    SeriesItem,
    SourceMetadata,
    Topic,
    TranscriptCue,
    generate_id,
)
from vault.db.session import async_session_maker, init_db


@pytest.mark.asyncio
async def test_models_crud():
    await init_db()

    async with async_session_maker() as session:
        # Create Series Resource
        series = Resource(
            id=generate_id("ser"),
            type="series",
            title="MIT 6.006",
            original_title="MIT 6.006 Fall 2011",
        )
        series.data = {"instructor": "Erik Demaine"}
        session.add(series)

        # Create Video Resource
        video = Resource(
            id=generate_id("res"),
            type="video",
            title="01. Peak Finding",
            original_title="Lecture 1: Peak Finding",
        )
        video.data = {"duration_seconds": 3012.0, "file_path": "Lectures/MIT/ep01.mp4"}
        session.add(video)

        # Create SeriesItem link
        item = SeriesItem(
            parent_series_id=series.id,
            child_resource_id=video.id,
            order_index=1,
        )
        session.add(item)

        # Create Topic
        topic = Topic(
            resource_id=video.id,
            title="1D Peak Finding",
            start_seconds=120.0,
            end_seconds=640.0,
            order_index=1,
        )
        session.add(topic)

        # Create TranscriptCue
        cue = TranscriptCue(
            resource_id=video.id,
            start_seconds=120.0,
            end_seconds=125.0,
            text="Let us begin with the one-dimensional case.",
        )
        session.add(cue)

        # Create SourceMetadata
        src = SourceMetadata(
            resource_id=video.id,
            provider="youtube",
            source_id="dQw4w9WgXcQ",
        )
        src.raw_payload = {"view_count": 50000}
        session.add(src)

        # Create Job
        job = Job(
            series_id=series.id,
            status="queued",
            progress_percent=0.0,
        )
        session.add(job)

        # Create Collection and CollectionItem
        col = Collection(
            id=generate_id("col"),
            title="MIT 6.006",
            context="course",
        )
        col.data = {"institution": "MIT"}
        session.add(col)

        c_item = CollectionItem(
            collection_id=col.id,
            resource_id=video.id,
            context="lecture_video",
            order_index=1,
        )
        session.add(c_item)

        await session.commit()

        # Query back and verify
        fetched_col = await session.get(Collection, col.id)
        assert fetched_col is not None
        assert fetched_col.context == "course"

        ci_list = (await session.exec(select(CollectionItem).where(CollectionItem.collection_id == col.id))).all()
        assert len(ci_list) == 1
        assert ci_list[0].context == "lecture_video"
        fetched_series = await session.get(Resource, series.id)
        assert fetched_series is not None
        assert fetched_series.type == "series"
        assert fetched_series.data["instructor"] == "Erik Demaine"

        fetched_video = await session.get(Resource, video.id)
        assert fetched_video is not None
        assert fetched_video.data["duration_seconds"] == 3012.0

        topics = (await session.exec(select(Topic).where(Topic.resource_id == video.id))).all()
        assert len(topics) == 1
        assert topics[0].title == "1D Peak Finding"

        cues = (await session.exec(select(TranscriptCue).where(TranscriptCue.resource_id == video.id))).all()
        assert len(cues) == 1
        assert "one-dimensional" in cues[0].text

        src_meta = (await session.exec(select(SourceMetadata).where(SourceMetadata.resource_id == video.id))).first()
        assert src_meta is not None
        assert src_meta.raw_payload["view_count"] == 50000
