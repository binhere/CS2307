"""Photo sync: manifest reading, resumable download, CRC-checked reads and verified uploads.

The server whose RAM flips bits showed why each photo is checked when read and read back after
upload; these tests flip bits on purpose (in a local copy of the archive and in storage).

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
    """A zip archive with one small, distinct, deflated file per key, as the photo archive is laid out."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, key in enumerate(keys):
            archive.writestr(key, f"photo {index} ".encode() * (index + 50))
    return buffer.getvalue()


def archive_content(body: bytes, key: str) -> bytes:
    """The original content of one member."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        return archive.read(key)


def flip_bit_in_member(body: bytes, key: str) -> bytes:
    """A copy of the archive with one bit flipped inside the member's compressed data."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        info = archive.getinfo(key)
    data_offset = info.header_offset + photos.LOCAL_HEADER.size + len(info.filename.encode()) + len(info.extra)
    corrupted = bytearray(body)
    corrupted[data_offset + info.compress_size // 2] ^= 0x01
    return bytes(corrupted)


class ArchiveServer:
    """A local HTTP server for one file that honours Range headers (start- and start-end) unless told not to."""

    def __init__(self, body: bytes, content_type: str = "application/octet-stream", honour_range: bool = True):
        self.body = body
        self.range_headers = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - name required by BaseHTTPRequestHandler
                requested_range = self.headers.get("Range")
                server.range_headers.append(requested_range)
                if requested_range and honour_range:
                    first, _, last = requested_range.split("=")[1].partition("-")
                    start = int(first)
                    end = int(last) if last else len(server.body) - 1
                    payload = server.body[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(server.body)}")
                else:
                    payload = server.body
                    self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
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


class StoredObject:
    """Stands in for the HTTP response minio.Minio.get_object returns."""

    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def read(self):
        return self.stream.read()

    def close(self):
        pass

    def release_conn(self):
        pass


class FakeMinio:
    """Stands in for minio.Minio: keeps objects in a dict and can flip a bit in what it stores."""

    def __init__(self, existing=None, damaged_puts=None):
        self.objects = dict(existing or {})
        self.damaged_puts = dict(damaged_puts or {})
        self.put_counts = {}
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
        self.put_counts[key] = self.put_counts.get(key, 0) + 1
        if self.damaged_puts.get(key, 0) > 0:
            self.damaged_puts[key] -= 1
            data = bytes([data[0] ^ 0x01]) + data[1:]
        self.objects[key] = data

    def get_object(self, bucket, key):
        return StoredObject(self.objects[key])


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


class MemberContentTests(unittest.TestCase):
    def test_remote_table_of_contents_and_member_read_back_intact(self):
        body = build_archive(PHOTO_KEYS)
        server = ArchiveServer(body)
        try:
            members = photos.remote_members(server.url, plain_http())
            content = photos.member_content(
                lambda offset, length: photos.read_remote_range(plain_http(), server.url, offset, length),
                members[PHOTO_KEYS[2]],
            )
        finally:
            server.close()
        self.assertEqual(set(members), set(PHOTO_KEYS))
        self.assertEqual(content, archive_content(body, PHOTO_KEYS[2]))

    def test_a_flipped_bit_fails_the_crc_check(self):
        body = build_archive(PHOTO_KEYS)
        corrupted = flip_bit_in_member(body, PHOTO_KEYS[1])
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            info = archive.getinfo(PHOTO_KEYS[1])
        with self.assertRaises(photos.PhotoIntegrityError):
            photos.member_content(lambda offset, length: corrupted[offset:offset + length], info)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.body = build_archive(PHOTO_KEYS)
        self.server = ArchiveServer(self.body)
        self.directory = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.directory.name)

    def tearDown(self):
        self.server.close()
        self.directory.cleanup()

    def sync(self, client, manifest_keys=PHOTO_KEYS, **options):
        photos.sync_photos(client, "photos", set(manifest_keys), self.server.url, len(self.body),
                           self.work_dir, plain_http(), **options)

    def assert_intact(self, client, keys=PHOTO_KEYS):
        for key in keys:
            self.assertEqual(client.objects[key], archive_content(self.body, key), key)

    def test_a_few_missing_photos_are_read_from_the_url_without_a_download(self):
        client = FakeMinio(existing={PHOTO_KEYS[0]: archive_content(self.body, PHOTO_KEYS[0])})
        self.sync(client)
        self.assert_intact(client)
        self.assertTrue(all(header and not header.endswith("-") for header in self.server.range_headers),
                        self.server.range_headers)
        self.assertFalse((self.work_dir / "images.zip").exists())

    def test_many_missing_photos_download_the_archive_once_and_delete_it(self):
        client = FakeMinio()
        self.sync(client, download_threshold=0)
        self.assert_intact(client)
        self.assertIn(None, self.server.range_headers)
        self.assertFalse((self.work_dir / "images.zip").exists())

    def test_a_corrupt_local_copy_is_read_again_from_the_url(self):
        (self.work_dir / "images.zip").write_bytes(flip_bit_in_member(self.body, PHOTO_KEYS[1]))
        client = FakeMinio()
        self.sync(client, download_threshold=0)
        self.assert_intact(client)
        self.assertEqual(client.put_counts[PHOTO_KEYS[1]], 1)

    def test_a_bit_flipped_in_storage_is_uploaded_again(self):
        client = FakeMinio(damaged_puts={PHOTO_KEYS[2]: 1})
        self.sync(client)
        self.assert_intact(client)
        self.assertEqual(client.put_counts[PHOTO_KEYS[2]], 2)

    def test_a_photo_that_never_stores_intact_is_reported_after_the_others(self):
        client = FakeMinio(damaged_puts={PHOTO_KEYS[0]: photos.UPLOAD_ATTEMPTS})
        with self.assertRaisesRegex(SystemExit, "1 of 3 photos could not be stored intact"):
            self.sync(client)
        self.assert_intact(client, PHOTO_KEYS[1:])
        self.assertEqual(client.put_counts[PHOTO_KEYS[0]], photos.UPLOAD_ATTEMPTS)

    def test_nothing_missing_touches_neither_the_url_nor_the_photos(self):
        client = FakeMinio(existing={key: b"" for key in PHOTO_KEYS})
        self.sync(client)
        self.assertEqual(self.server.range_headers, [])
        self.assertEqual(client.put_counts, {})

    def test_verify_existing_replaces_damaged_photos_only(self):
        existing = {key: archive_content(self.body, key) for key in PHOTO_KEYS}
        existing[PHOTO_KEYS[1]] = b"flipped"
        client = FakeMinio(existing=existing)
        self.sync(client, verify_existing=True)
        self.assert_intact(client)
        self.assertEqual(client.put_counts, {PHOTO_KEYS[1]: 1})

    def test_a_manifest_key_the_archive_lacks_stops_the_run_before_any_upload(self):
        client = FakeMinio()
        with self.assertRaisesRegex(SystemExit, "not in the archive"):
            self.sync(client, PHOTO_KEYS + ["images/Căn hộ chung cư/Phường Tân Định/303/missing.jpg"])
        self.assertEqual(client.put_counts, {})

    def test_a_missing_bucket_is_created(self):
        client = FakeMinio()
        client.has_bucket = False
        self.sync(client)
        self.assertTrue(client.has_bucket)
        self.assert_intact(client)


if __name__ == "__main__":
    unittest.main()
