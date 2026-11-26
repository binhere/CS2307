"""Posting a listing: photo checks, the stored row, storage failures and the posting endpoint.

A posted listing must land as a "user" row the catalog seed never deletes, with photos the
existing image route can serve, and a failed save must not leave orphan photos behind.
"""

import asyncio
import io
import json
import unittest
from datetime import date
from unittest.mock import patch

import httpx
from PIL import Image
from sqlalchemy.exc import OperationalError

from src.search import repository as search_repository_module
from src.services.listing_submission import storage
from src.services.listing_submission.validation import validate_listing
from src.services.minio.minio_client import ImageStorageUnavailableError
from tests.test_listing_validation import PLAN_EXAMPLE

TODAY = date(2026, 11, 26)


def image_bytes(width, height, image_format="JPEG"):
    """An in-memory image of the given size and format."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 120, 200)).save(buffer, format=image_format)
    return buffer.getvalue()


def valid_listing():
    """The plan example after validation: canonical codes, metadata and all."""
    report = validate_listing(json.loads(json.dumps(PLAN_EXAMPLE)), 0, TODAY)
    assert report.is_valid, report.errors
    return report.listing


class FakeImageStore:
    def __init__(self, error=None):
        self.stored, self.removed, self.error = {}, [], error

    def put_image(self, key, content, content_type):
        if self.error:
            raise self.error
        self.stored[key] = content_type

    def remove_images(self, keys):
        self.removed.extend(keys)


class FakeDatabase:
    def __init__(self, write_error=None, earlier_ids=()):
        self.writes, self.write_error, self.earlier_ids = [], write_error, list(earlier_ids)

    def fetch_write(self, query, **parameters):
        return [{"value": 9000000001}]

    def fetch_mappings(self, query, **parameters):
        return [{"id": earlier_id} for earlier_id in self.earlier_ids]

    def execute_write(self, query, **parameters):
        if self.write_error:
            raise self.write_error
        self.writes.append((query, parameters))


class PhotoTests(unittest.TestCase):
    def test_real_images_within_limits_pass(self):
        photo = storage.check_photo(image_bytes(1280, 960), 1280, 1_500_000, "Ảnh 1")
        self.assertEqual(photo.image_format, "JPEG")
        self.assertEqual(storage.check_photo(image_bytes(480, 360, "PNG"), 480, 200_000, "Ảnh 1").image_format, "PNG")

    def test_oversized_fake_and_unsupported_files_are_refused(self):
        cases = {
            "cạnh dài nhất 1600 px": (image_bytes(900, 1600), 1280, 1_500_000),
            "không phải ảnh": (b"%PDF-1.7 not an image", 1280, 1_500_000),
            "JPEG, WebP hoặc PNG": (image_bytes(100, 100, "GIF"), 1280, 1_500_000),
            "tối đa 0 KB": (image_bytes(100, 100), 1280, 10),
        }
        for expected, (content, side, size) in cases.items():
            with self.subTest(expected=expected), self.assertRaises(storage.PhotoError) as raised:
                storage.check_photo(content, side, size, "Ảnh 1")
            self.assertIn(expected, str(raised.exception))

    def test_a_listing_needs_one_to_twenty_photos_with_matching_thumbnails(self):
        gallery, thumbnail = image_bytes(1280, 960), image_bytes(480, 360)
        self.assertEqual(len(storage.check_photos([gallery, gallery], [thumbnail, thumbnail])), 2)
        for galleries, thumbnails in (([], []), ([gallery] * 21, [thumbnail] * 21), ([gallery], [])):
            with self.assertRaises(storage.PhotoError):
                storage.check_photos(galleries, thumbnails)


class RowTests(unittest.TestCase):
    def test_a_posted_listing_becomes_a_user_row_in_the_catalog_conventions(self):
        keys = ["images/user-listings/9000000001/gallery/001.jpg"]
        row = storage.listing_row(valid_listing(), 9000000001, 7, keys)
        self.assertEqual((row["id"], row["listing_id"], row["source"], row["owner_user_id"]), (9000000001, 9000000001, "user", 7))
        self.assertEqual((row["property_type"], row["legal_status"], row["furnishing"]),
                         ("Căn hộ chung cư", "Hợp đồng mua bán", "Cơ bản"))
        self.assertEqual((row["district"], row["former_admin_area"]), ("Phường Cát Lái", "Quận 2"))
        self.assertEqual(row["latitude_longitude"], "10.7792, 106.7571")
        self.assertEqual(row["address"], "Căn hộ DEFINE, Đường Số 60 - TML, Phường Cát Lái, Hồ Chí Minh")
        self.assertEqual(json.loads(row["images"]), keys)
        self.assertEqual(json.loads(row["submission"])["metadata"]["nearby_amenities"][0]["name"], "Rạch Chiếc")
        self.assertIn("Phường Cát Lái", row["search_text"])
        self.assertEqual(row["content_fingerprint"], storage.listing_fingerprint(valid_listing()))
        # The image route only serves keys under images/, so posted photos must live there.
        self.assertTrue(storage.photo_key(9000000001, "thumbnails", 3, "WEBP").startswith("images/"))


class FingerprintTests(unittest.TestCase):
    def test_the_same_content_matches_whatever_its_posting_date(self):
        listing = valid_listing()
        later = {**listing, "posted_date": "2026-11-27"}
        changed = {**listing, "price_billion_vnd": 44.0}
        self.assertEqual(storage.listing_fingerprint(listing), storage.listing_fingerprint(later))
        self.assertNotEqual(storage.listing_fingerprint(listing), storage.listing_fingerprint(changed))
        self.assertEqual(len(storage.listing_fingerprint(listing)), 64)


class SaveTests(unittest.TestCase):
    def photos(self):
        return storage.check_photos([image_bytes(1280, 960)] * 2, [image_bytes(480, 360)] * 2)

    def test_photos_are_stored_then_the_row_is_written(self):
        images, database = FakeImageStore(), FakeDatabase()
        with patch.object(storage, "minio_client", images), patch.object(storage, "postgres_client", database):
            saved = storage.save_listing(valid_listing(), self.photos(), 7)
        self.assertEqual(saved, {"id": 9000000001, "listing_id": 9000000001})
        self.assertEqual(sorted(images.stored), [
            "images/user-listings/9000000001/gallery/001.jpg", "images/user-listings/9000000001/gallery/002.jpg",
            "images/user-listings/9000000001/thumbnails/001.jpg", "images/user-listings/9000000001/thumbnails/002.jpg"])
        query, parameters = database.writes[0]
        self.assertTrue(query.startswith("INSERT INTO properties"))
        self.assertEqual(parameters["source"], "user")

    def test_a_failed_row_write_removes_the_uploaded_photos(self):
        images = FakeImageStore()
        database = FakeDatabase(write_error=OperationalError("INSERT", {}, Exception("down")))
        with patch.object(storage, "minio_client", images), patch.object(storage, "postgres_client", database), \
                self.assertRaises(OperationalError):
            storage.save_listing(valid_listing(), self.photos(), 7)
        self.assertEqual(sorted(images.removed), sorted(images.stored))

    def test_the_vector_is_stored_when_the_embedding_service_answers(self):
        class FakeModel:
            model_name = "fake-embedding"

            def __init__(self, vectors):
                self.vectors = vectors

            def encode(self, texts, kind):
                assert kind == "passage"
                return self.vectors

        database = FakeDatabase()
        database.fetch_mappings = lambda query, **parameters: [{"search_text": "Căn hộ Cát Lái"}]
        with patch.object(storage, "postgres_client", database), patch.object(storage, "embedding_model", FakeModel([[0.5, 0.25]])):
            self.assertTrue(storage.embed_listing(9000000001))
        self.assertEqual(database.writes[0][1]["embedding"], "[0.50000000,0.25000000]")
        with patch.object(storage, "postgres_client", database), patch.object(storage, "embedding_model", FakeModel(None)):
            self.assertFalse(storage.embed_listing(9000000001))


class SemanticSearchGateTests(unittest.TestCase):
    def test_only_catalog_rows_decide_whether_semantic_search_runs(self):
        queries = []

        def fetch_mappings(query, **parameters):
            queries.append(query)
            return [{"embedded": 3857, "total": 3857}]

        with patch.object(search_repository_module.postgres_client, "fetch_mappings", fetch_mappings):
            self.assertEqual(search_repository_module.SearchRepository().embedding_coverage(), (3857, 3857))
        self.assertIn("source = 'catalog'", queries[0])


class PostingApiTests(unittest.TestCase):
    def post(self, listing, galleries, thumbnails, user=None, images=None, database=None):
        """POST /v1/listings as multipart through the ASGI app, with fake storage."""
        from src.main import app
        from src.services.auth.dependencies import get_current_user

        files = [("photos", (f"{index}.jpg", content, "image/jpeg")) for index, content in enumerate(galleries)]
        files += [("thumbnails", (f"{index}.jpg", content, "image/jpeg")) for index, content in enumerate(thumbnails)]

        async def send():
            if user is not None:
                app.dependency_overrides[get_current_user] = lambda: user
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    return await client.post("/v1/listings", data={"listing": json.dumps(listing, ensure_ascii=False)}, files=files)
            finally:
                app.dependency_overrides.pop(get_current_user, None)

        with patch.object(storage, "minio_client", images or FakeImageStore()), \
                patch.object(storage, "postgres_client", database or FakeDatabase()), \
                patch.object(storage, "embed_listing", lambda listing_id: True):
            return asyncio.run(send())

    def test_a_valid_listing_is_saved(self):
        response = self.post(PLAN_EXAMPLE, [image_bytes(1280, 960)], [image_bytes(480, 360)], user={"id": 7, "username": "lan"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["data"]["id"], 9000000001)

    def test_refusals_carry_a_reason(self):
        user = {"id": 7, "username": "lan"}
        invalid = self.post({"title": "Ngắn"}, [image_bytes(1280, 960)], [image_bytes(480, 360)], user=user)
        self.assertEqual((invalid.status_code, invalid.json()["errors"][0]["msg"]), (400, "INVALID_LISTING"))
        self.assertFalse(invalid.json()["data"]["report"]["is_valid"])
        too_wide = self.post(PLAN_EXAMPLE, [image_bytes(2000, 1000)], [image_bytes(480, 240)], user=user)
        self.assertEqual((too_wide.status_code, too_wide.json()["errors"][0]["msg"]), (400, "INVALID_PHOTO"))
        no_store = self.post(PLAN_EXAMPLE, [image_bytes(1280, 960)], [image_bytes(480, 360)], user=user,
                             images=FakeImageStore(error=ImageStorageUnavailableError("off")))
        self.assertEqual((no_store.status_code, no_store.json()["errors"][0]["msg"]), (503, "IMAGE_STORAGE_UNAVAILABLE"))

    def test_the_same_listing_posted_again_points_at_the_first_copy(self):
        images = FakeImageStore()
        response = self.post(PLAN_EXAMPLE, [image_bytes(1280, 960)], [image_bytes(480, 360)], user={"id": 7, "username": "lan"},
                             images=images, database=FakeDatabase(earlier_ids=[9000000005]))
        self.assertEqual((response.status_code, response.json()["errors"][0]["msg"]), (409, "DUPLICATE_LISTING"))
        self.assertEqual(response.json()["data"]["id"], 9000000005)
        self.assertEqual(images.stored, {})

    def test_anonymous_callers_cannot_post(self):
        response = self.post(PLAN_EXAMPLE, [image_bytes(1280, 960)], [image_bytes(480, 360)])
        self.assertEqual(response.status_code, 401)

    def test_the_form_choices_are_public(self):
        from src.main import app

        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.get("/v1/listings/reference")

        data = asyncio.run(send()).json()["data"]
        self.assertEqual(len(data["wards"]), 168)
        self.assertIn("Quận 2", data["former_districts"])
        self.assertEqual(data["property_types"][0], {"code": "can_ho_chung_cu", "label": "Căn hộ chung cư"})
        # The form narrows each place list to the other's choice with these two maps.
        self.assertEqual(data["ward_former_districts"]["Phường Chánh Hiệp"], ["Thành phố Thủ Dầu Một"])
        self.assertEqual(data["former_district_groups"]["Quận 2"], "Thành phố Thủ Đức")


class LocateApiTests(unittest.TestCase):
    def locate(self, latitude, longitude):
        from src.main import app

        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.get("/v1/listings/locate", params={"latitude": latitude, "longitude": longitude})

        return asyncio.run(send())

    def test_a_point_on_the_map_names_its_ward_and_former_districts(self):
        data = self.locate(10.7792, 106.7571).json()["data"]
        self.assertEqual(data, {"is_in_city": True, "ward": "Phường Cát Lái", "former_districts": ["Thành phố Thủ Đức"]})

    def test_a_point_outside_the_city_names_nothing(self):
        data = self.locate(10.957, 106.843).json()["data"]  # Biên Hòa, Đồng Nai
        self.assertEqual(data, {"is_in_city": False, "ward": None, "former_districts": []})

    def test_coordinates_must_be_numbers(self):
        self.assertEqual(self.locate("abc", 106.7).status_code, 400)


if __name__ == "__main__":
    unittest.main()
