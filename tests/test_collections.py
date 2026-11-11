import pytest
from httpx import ASGITransport, AsyncClient
from vault.db.models import Collection, CollectionItem, Resource, generate_id
from vault.db.session import async_session_maker, init_db
from vault.main import app


@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()


@pytest.mark.asyncio
async def test_collections_crud_and_contexts():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create a Collection with arbitrary context
        col_resp = await client.post(
            "/api/v1/collections",
            json={
                "title": "Machine Learning Foundations",
                "context": "subject_specialization",
                "data": {
                    "overview": "Essential mathematics and neural network basics.",
                    "institution": "Stanford",
                },
            },
        )
        assert col_resp.status_code == 201
        col_data = col_resp.json()
        col_id = col_data["id"]
        assert col_data["context"] == "subject_specialization"
        assert col_data["data"]["institution"] == "Stanford"

        # 2. Create a Video Resource and a Slides Resource
        vid_id = generate_id("res")
        slides_id = generate_id("res")

        async with async_session_maker() as session:
            vid = Resource(
                id=vid_id,
                type="video",
                title="Linear Regression Basics",
            )
            vid.data = {"duration_seconds": 3600.0}
            session.add(vid)

            slides = Resource(
                id=slides_id,
                type="slides",
                title="Linear Regression Lecture Slides",
            )
            slides.data = {"slide_count": 40}
            session.add(slides)
            await session.commit()

        # 3. Add Video to Collection with item context 'primary_lecture'
        link_vid = await client.post(
            f"/api/v1/collections/{col_id}/items",
            json={
                "resource_id": vid_id,
                "context": "primary_lecture",
                "order_index": 1,
            },
        )
        assert link_vid.status_code == 201

        # 4. Add Slides to Collection with item context 'companion_slides'
        link_slides = await client.post(
            f"/api/v1/collections/{col_id}/items",
            json={
                "resource_id": slides_id,
                "context": "companion_slides",
                "order_index": 1,
            },
        )
        assert link_slides.status_code == 201

        # 5. Fetch Collection and verify items have their specific contexts
        get_col = await client.get(f"/api/v1/collections/{col_id}")
        assert get_col.status_code == 200
        items = get_col.json()["items"]
        assert len(items) == 2
        assert items[0]["resource_id"] == vid_id
        assert items[0]["context"] == "primary_lecture"
        assert items[1]["resource_id"] == slides_id
        assert items[1]["context"] == "companion_slides"

        # 6. Fetch Video Resource with ?include=collections and verify it knows its membership context
        get_vid = await client.get(f"/api/v1/resources/{vid_id}?include=collections")
        assert get_vid.status_code == 200
        vid_data = get_vid.json()
        assert len(vid_data["collections"]) == 1
        assert vid_data["collections"][0]["collection_id"] == col_id
        assert vid_data["collections"][0]["collection_context"] == "subject_specialization"
        assert vid_data["collections"][0]["context"] == "primary_lecture"

        # 7. Unlink one item
        del_item = await client.delete(f"/api/v1/collections/{col_id}/items/{slides_id}")
        assert del_item.status_code == 200

        # Verify only 1 item remains in collection
        get_col_after = await client.get(f"/api/v1/collections/{col_id}")
        assert len(get_col_after.json()["items"]) == 1

        # 8. Delete collection
        del_col = await client.delete(f"/api/v1/collections/{col_id}")
        assert del_col.status_code == 200
        get_deleted_col = await client.get(f"/api/v1/collections/{col_id}")
        assert get_deleted_col.status_code == 404

        # Resources should still exist intact!
        res_still_there = await client.get(f"/api/v1/resources/{vid_id}")
        assert res_still_there.status_code == 200
