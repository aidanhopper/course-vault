import pytest
from httpx import ASGITransport, AsyncClient
from vault.db.models import Resource, Topic, TranscriptCue, generate_id
from vault.db.session import async_session_maker, init_db
from vault.main import app


@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


@pytest.mark.asyncio
async def test_series_and_resources_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a Series as a Resource
        resp = await client.post(
            "/api/v1/resources",
            json={
                "type": "series",
                "title": "Introduction to Computer Science",
                "data": {
                    "instructor": "David Malan",
                    "overview": "CS50 entry course.",
                },
            },
        )
        assert resp.status_code == 201
        series_data = resp.json()
        series_id = series_data["id"]
        assert series_data["type"] == "series"
        assert series_data["title"] == "Introduction to Computer Science"

        # 2. Add a video resource directly in DB
        video_id = generate_id("res")
        async with async_session_maker() as session:
            video = Resource(
                id=video_id,
                type="video",
                title="01. Scratch and C",
                original_title="Lecture 1 - Scratch and C",
            )
            video.data = {"duration_seconds": 3600.0}
            session.add(video)

            # Add a topic
            topic = Topic(
                resource_id=video_id,
                title="Conditionals and Loops",
                start_seconds=300.0,
                end_seconds=900.0,
                order_index=1,
            )
            session.add(topic)

            # Add transcript
            cue = TranscriptCue(
                resource_id=video_id,
                start_seconds=300.0,
                end_seconds=305.0,
                text="In C, an if statement takes a boolean expression.",
            )
            session.add(cue)
            await session.commit()

        # 3. Link video to series
        link_resp = await client.post(
            f"/api/v1/resources/{series_id}/items",
            json={"resource_id": video_id, "order_index": 1},
        )
        assert link_resp.status_code == 201

        # 4. Fetch Series detail with items
        series_detail = await client.get(f"/api/v1/resources/{series_id}?include=items")
        assert series_detail.status_code == 200
        detail_data = series_detail.json()
        assert len(detail_data["items"]) == 1
        assert detail_data["items"][0]["resource_id"] == video_id

        # 5. Get Resource with aspects included
        res_resp = await client.get(
            f"/api/v1/resources/{video_id}?include=topics,transcript"
        )
        assert res_resp.status_code == 200
        res_data = res_resp.json()
        assert res_data["id"] == video_id
        assert len(res_data["topics"]) == 1
        assert res_data["topics"][0]["title"] == "Conditionals and Loops"
        assert len(res_data["transcript"]) == 1
        assert "boolean expression" in res_data["transcript"][0]["text"]

        # 6. Test /transcript with different formats
        tr_json = await client.get(f"/api/v1/resources/{video_id}/transcript?format=json")
        assert tr_json.status_code == 200
        assert isinstance(tr_json.json(), list)

        tr_text = await client.get(f"/api/v1/resources/{video_id}/transcript?format=text")
        assert tr_text.status_code == 200
        assert "boolean expression" in tr_text.text

        tr_vtt = await client.get(f"/api/v1/resources/{video_id}/transcript?format=vtt")
        assert tr_vtt.status_code == 200
        assert "WEBVTT" in tr_vtt.text

        # 7. Test /topics endpoint directly
        top_resp = await client.get(f"/api/v1/resources/{video_id}/topics")
        assert top_resp.status_code == 200
        assert len(top_resp.json()) == 1

        # 8. Test list resources with filter
        list_videos = await client.get("/api/v1/resources?type=video")
        assert list_videos.status_code == 200
        assert any(r["id"] == video_id for r in list_videos.json())

        list_series = await client.get("/api/v1/resources?type=series")
        assert list_series.status_code == 200
        assert any(s["id"] == series_id for s in list_series.json())

        # 9. Test unlinking item from series
        unlink_resp = await client.delete(f"/api/v1/resources/{series_id}/items/{video_id}")
        assert unlink_resp.status_code == 200

        # Video should still exist independently
        res_after_unlink = await client.get(f"/api/v1/resources/{video_id}")
        assert res_after_unlink.status_code == 200

        # 10. Delete video resource
        del_resp = await client.delete(f"/api/v1/resources/{video_id}")
        assert del_resp.status_code == 200
        get_deleted = await client.get(f"/api/v1/resources/{video_id}")
        assert get_deleted.status_code == 404


@pytest.mark.asyncio
async def test_import_and_job_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Submit an import
        resp = await client.post(
            "/api/v1/imports",
            json={"url": "https://www.youtube.com/playlist?list=PLB2BE3D6041052A62"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        job_id = data["job_id"]

        # Check job status
        job_resp = await client.get(f"/api/v1/jobs/{job_id}")
        assert job_resp.status_code == 200
        job_data = job_resp.json()
        assert job_data["id"] == job_id
        assert job_data["status"] in ("queued", "extracting", "cleaning", "downloading", "failed")

        # Cancel job
        cancel_resp = await client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] in ("cancelled", "completed", "failed")
