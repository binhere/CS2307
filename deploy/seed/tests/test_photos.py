"""Photo sync: manifest reading, resumable archive download and uploading only missing photos.

Run with: python -m pytest deploy/seed/tests (needs deploy/seed/requirements.txt installed).
"""

import csv
import http.server
import io
import sys
import tempfile
import threading
import unicodedata
import unittest
import zipfile
from pathlib import Path

import urllib3

SEED_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SEED_DIRECTORY))

import photos  # noqa: E402

PHOTO_KEYS = [
    "images/Căn hộ chung cư/Phường Tân Định/101/001_101_001.jpg",
    "images/Căn hộ chung cư/Phường Tân Định/101/001_101_002.jpg",
    "images/Nhà biệt thự, liền kề/Phường Long Trường/202/002_202_001.jpg",
]


def build_archive(keys) -> bytes:
    """A zip archive with one small, distinct file per key, as the photo archive is laid out."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, key in enumerate(keys):
            archive.writestr(key, f"photo {index} ".encode() * (index + 50))
    return buffer.getvalue()


class ArchiveServer:
    """A local HTTP server for one file that honours Range headers unless told to ignore them."""

    def __init__(self, body: bytes, content_type: str = "application/octet-stream", honour_range: bool = True):
        self.body = body
        self.range_headers = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - name required by BaseHTTPRequestHandler
                requested_range = self.headers.get("Range")
                server.range_headers.append(requested_range)
                start = int(requested_range.split("=")[1].split("-")[0]) if requested_range and honour_range else 0
                payload = server.body[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(server.body) - 1}/{len(server.body)}")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/images.zip"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeMinio:
    """Stands in for minio.Minio: keeps uploaded objects in a dict."""

    def __init__(self, existing=()):
        self.objects = {key: b"" for key in existing}
        self.has_bucket = True

    def bucket_exists(self, bucket):
        return self.has_bucket

    def make_bucket(self, bucket):
        self.has_bucket = True

    def list_objects(self, bucket, prefix, recursive):
        return [type("Item", (), {"object_name": key}) for key in self.objects if key.startswith(prefix)]

    def put_object(self, bucket, key, stream, length, content_type):
        data = stream.read(length)
        assert len(data) == length
        self.objects[key] = data


def plain_http() -> urllib3.PoolManager:
    """A pool without retries, so a test failure shows at once."""
    return urllib3.PoolManager(retries=False)


class ManifestTests(unittest.TestCase):
    def test_keys_are_read_in_nfc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images_manifest.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["listing_id", "object_key"])
                writer.writerow(["101", unicodedata.normalize("NFD", PHOTO_KEYS[0])])
            self.assertEqual(photos.read_manifest_keys(path), {PHOTO_KEYS[0]})


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.body = build_archive(PHOTO_KEYS)
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "images.zip"

    def tearDown(self):
        self.directory.cleanup()

    def test_a_partial_download_continues_from_where_it_stopped(self):
        self.path.write_bytes(self.body[:100])
        server = ArchiveServer(self.body)
        try:
            photos.download_archive(server.url, self.path, len(self.body), plain_http())
        finally:
            server.close()
        self.assertEqual(server.range_headers, ["bytes=100-"])
        self.assertEqual(self.path.read_bytes(), self.body)

    def test_a_server_that_ignores_the_range_restarts_the_file(self):
        self.path.write_bytes(self.body[:100])
        server = ArchiveServer(self.body, honour_range=False)
        try:
            photos.download_archive(server.url, self.path, len(self.body), plain_http())
        finally:
            server.close()
        self.assertEqual(self.path.read_bytes(), self.body)

    def test_a_complete_file_is_not_downloaded_again(self):
        self.path.write_bytes(self.body)
        server = ArchiveServer(self.body)
        try:
            photos.download_archive(server.url, self.path, len(self.body), plain_http())
        finally:
            server.close()
        self.assertEqual(server.range_headers, [])

    def test_a_web_page_instead_of_the_archive_fails_with_a_clear_error(self):
        server = ArchiveServer(b"<html>quota exceeded</html>", content_type="text/html; charset=utf-8")
        try:
            with self.assertRaisesRegex(photos.ArchiveDownloadError, "web page"):
                photos.download_archive(server.url, self.path, len(self.body), plain_http())
        finally:
            server.close()

    def test_a_file_that_is_not_a_zip_archive_fails(self):
        body = b"x" * len(self.body)
        server = ArchiveServer(body)
        try:
            with self.assertRaisesRegex(photos.ArchiveDownloadError, "not a zip archive"):
                photos.download_archive(server.url, self.path, len(body), plain_http())
        finally:
            server.close()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.body = build_archive(PHOTO_KEYS)
        self.server = ArchiveServer(self.body)
        self.directory = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.directory.name)

    def tearDown(self):
        self.server.close()
        self.directory.cleanup()

    def sync(self, client, manifest_keys):
        photos.sync_photos(client, "photos", set(manifest_keys), self.server.url, len(self.body),
                           self.work_dir, plain_http())

    def test_only_missing_photos_are_uploaded_and_the_archive_is_deleted(self):
        client = FakeMinio(existing=[PHOTO_KEYS[0]])
        self.sync(client, PHOTO_KEYS)
        self.assertEqual(client.objects[PHOTO_KEYS[0]], b"")
        with zipfile.ZipFile(io.BytesIO(self.body)) as archive:
            for key in PHOTO_KEYS[1:]:
                self.assertEqual(client.objects[key], archive.read(key))
        self.assertFalse((self.work_dir / "images.zip").exists())

    def test_nothing_missing_downloads_nothing(self):
        client = FakeMinio(existing=PHOTO_KEYS)
        self.sync(client, PHOTO_KEYS)
        self.assertEqual(self.server.range_headers, [])

    def test_a_manifest_key_the_archive_lacks_stops_the_run(self):
        client = FakeMinio()
        with self.assertRaisesRegex(SystemExit, "not in the archive"):
            self.sync(client, PHOTO_KEYS + ["images/Căn hộ chung cư/Phường Tân Định/303/missing.jpg"])

    def test_a_missing_bucket_is_created(self):
        client = FakeMinio()
        client.has_bucket = False
        self.sync(client, PHOTO_KEYS)
        self.assertTrue(client.has_bucket)
        self.assertEqual(set(client.objects), set(PHOTO_KEYS))


if __name__ == "__main__":
    unittest.main()
