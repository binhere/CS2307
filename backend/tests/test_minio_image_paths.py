"""MinIO image access: safe object keys, and fast failure when images are unavailable."""

import time
import unittest
from unittest.mock import patch

import httpx
import urllib3

from src.services.minio import minio_client as minio_module
from src.services.minio.minio_client import ImageStorageUnavailableError, MinioClient


class MinioImagePathTests(unittest.TestCase):
    def setUp(self):
        self.client = MinioClient(
            {
                "minio_end_point": "minio:9000",
                "minio_access_key_id": "test",
                "minio_secret_access_key": "test",
                "minio_bucket_name": "cs2307",
                "minio_secure": False,
            }
        )

    def test_extracts_object_key_from_database_url(self):
        value = "http://example:9000/cs2307/images/can-ho/45225431/photo.jpg"
        self.assertEqual(
            self.client.normalize_object_key(value),
            "images/can-ho/45225431/photo.jpg",
        )

    def test_accepts_object_key(self):
        self.assertEqual(
            self.client.normalize_object_key("images/nha-rieng/123/photo.webp"),
            "images/nha-rieng/123/photo.webp",
        )

    def test_rejects_non_property_and_traversal_paths(self):
        invalid_paths = (
            "avatars/user.jpg",
            "images/../secret",
            "",
            "images//photo.jpg",
        )
        for value in invalid_paths:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.client.normalize_object_key(value)


class MinioAvailabilityTests(unittest.TestCase):
    def test_missing_endpoint_leaves_images_disabled(self):
        client = MinioClient({"minio_end_point": "", "minio_bucket_name": "cs2307"})
        client.start()
        self.assertFalse(client.is_configured)
        with self.assertRaises(ImageStorageUnavailableError):
            client.minio_get_object("images/can-ho/1/photo.jpg")

    def test_http_client_gives_up_within_seconds(self):
        pool = minio_module.build_http_client()
        self.assertLessEqual(pool.connection_pool_kw["timeout"].connect_timeout, 5)
        self.assertLessEqual(pool.connection_pool_kw["retries"].total, 1)

    def test_unreachable_minio_fails_fast(self):
        # Port 9 (discard) on localhost refuses the connection immediately.
        client = MinioClient({
            "minio_end_point": "127.0.0.1:9", "minio_access_key_id": "test",
            "minio_secret_access_key": "test", "minio_bucket_name": "cs2307", "minio_secure": False,
        })
        client.start()
        started = time.monotonic()
        with self.assertRaises(urllib3.exceptions.HTTPError):
            client.minio_get_object("images/can-ho/1/photo.jpg")
        self.assertLess(time.monotonic() - started, 5)


class ImageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def request_image(self):
        from src.main import app

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/v1/properties/image", params={"path": "images/can-ho/1/photo.jpg"})

    async def test_unconfigured_storage_answers_503(self):
        unavailable = ImageStorageUnavailableError("MinIO is not configured")
        with patch("src.api.v1.endpoints.properties.views.view_properties.minio_client.minio_get_object",
                   side_effect=unavailable):
            response = await self.request_image()
        self.assertEqual(response.status_code, 503)

    async def test_unreachable_storage_answers_503(self):
        unreachable = urllib3.exceptions.MaxRetryError(None, "/cs2307/images/can-ho/1/photo.jpg")
        with patch("src.api.v1.endpoints.properties.views.view_properties.minio_client.minio_get_object",
                   side_effect=unreachable):
            response = await self.request_image()
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
