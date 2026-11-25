"""Make the MinIO bucket hold every listing photo the catalog references.

Runs from the seed image as its own CronJob (command: python /seed/photos.py). Safe to run any
number of times:
- Lists the bucket's images/ prefix (one request per 1,000 objects) and compares it with
  images_manifest.csv. Nothing missing: exits.
- Photos missing: downloads the archive (resuming a partial download), uploads exactly the
  missing members under their manifest key, checks that none is still missing, and deletes the
  archive. Objects that are not in the manifest are left alone.

Input: environment variables MINIO_END_POINT (host:port), MINIO_ACCESS_KEY_ID,
    MINIO_SECRET_ACCESS_KEY (a key that may write the bucket), MINIO_BUCKET_NAME and
    MINIO_SECURE ("true" for https); optional PHOTO_ARCHIVE_URL and PHOTO_ARCHIVE_SIZE (default:
    the archive the manifest was made from), PHOTO_WORK_DIR (default /tmp; needs room for the
    archive, about 9 GB) and SEED_DATA_DIR (default /seed/data, holding images_manifest.csv).
Output: exit code 0 when every manifest key is in the bucket; non-zero otherwise, so the Job
    retries. Progress goes to stdout.
"""

from __future__ import annotations

import csv
import mimetypes
import os
import re
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Iterable, Set

import certifi
import urllib3
from minio import Minio

DATA_DIR = Path(os.environ.get("SEED_DATA_DIR", "/seed/data"))
# images.zip on Lộc's Google Drive, shared with anyone who has the link; it holds exactly the files of
# data/gold/images_manifest.csv. A new archive needs a new URL and size here and a new manifest.
DEFAULT_ARCHIVE_URL = (
    "https://drive.usercontent.google.com/download?id=1JR6lhWzcJzc2DsgZb_jLiij3PdT_V7Wm&export=download&confirm=t"
)
DEFAULT_ARCHIVE_SIZE = 9_523_350_576
OBJECT_PREFIX = "images/"
ARCHIVE_FILE_NAME = "images.zip"
ZIP_SIGNATURE = b"PK\x03\x04"
DOWNLOAD_ATTEMPTS = 20
DOWNLOAD_RETRY_SECONDS = 15
DOWNLOAD_CHUNK_BYTES = 1 << 20
DOWNLOAD_LOG_EVERY_BYTES = 1 << 30
UPLOAD_LOG_EVERY = 2000
CONTENT_RANGE = re.compile(r"bytes (\d+)-\d+/(\d+)")


class ArchiveDownloadError(RuntimeError):
    """The archive could not be downloaded completely and intact."""


def log(message: str) -> None:
    """Print one progress line, flushed so it reaches the Job log immediately."""
    print(f"[photos] {message}", flush=True)


def required_setting(name: str) -> str:
    """Read a required environment variable.

    Raises:
        SystemExit: The variable is missing or empty.
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"[photos] missing required environment variable {name}")
    return value


def read_manifest_keys(path: Path) -> Set[str]:
    """Read the object keys the catalog references.

    Args:
        path: images_manifest.csv with the columns listing_id and object_key.

    Returns:
        set: Object keys in Unicode NFC, e.g. "images/<property type>/<ward>/<listing id>/<file>".
    """
    with path.open(encoding="utf-8", newline="") as stream:
        return {unicodedata.normalize("NFC", row["object_key"]) for row in csv.DictReader(stream)}


def existing_keys(client: Minio, bucket: str) -> Set[str]:
    """List the keys already in the bucket under the photo prefix.

    Returns:
        set: Object keys as stored.
    """
    return {item.object_name for item in client.list_objects(bucket, prefix=OBJECT_PREFIX, recursive=True)}


def download_archive(url: str, path: Path, expected_size: int, http: urllib3.PoolManager) -> None:
    """Download the archive to path, continuing a partial file with HTTP range requests.

    Args:
        url: Direct download URL that honours Range headers.
        path: Destination file; a partial file from an earlier attempt is continued.
        expected_size: The archive's exact size in bytes.
        http: Pool used for the requests.

    Raises:
        ArchiveDownloadError: The download still failed after DOWNLOAD_ATTEMPTS attempts, the
            server answered with a web page (sharing or quota changed), or the result is not a
            zip archive of the expected size.
    """
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        present = path.stat().st_size if path.exists() else 0
        if present == expected_size:
            break
        if present > expected_size:
            path.unlink()
            present = 0
        headers = {"Range": f"bytes={present}-"} if present else {}
        try:
            response = http.request("GET", url, headers=headers, preload_content=False, redirect=True)
            try:
                content_type = response.headers.get("Content-Type", "")
                if content_type.startswith("text/html"):
                    raise ArchiveDownloadError(
                        "the archive URL answered with a web page instead of the file; check that the "
                        "file is still shared with anyone who has the link and has not hit its download quota"
                    )
                if response.status == 206:
                    match = CONTENT_RANGE.match(response.headers.get("Content-Range", ""))
                    if match is None or int(match.group(1)) != present:
                        raise ArchiveDownloadError(f"unexpected Content-Range {response.headers.get('Content-Range')!r}")
                    mode = "ab"
                elif response.status == 200:
                    # The server ignored the range: it sends the whole file again.
                    mode, present = "wb", 0
                else:
                    raise ArchiveDownloadError(f"HTTP {response.status} from the archive URL")
                log(f"downloading the archive from byte {present:,} of {expected_size:,}")
                next_log = present + DOWNLOAD_LOG_EVERY_BYTES
                with path.open(mode) as stream:
                    for chunk in response.stream(DOWNLOAD_CHUNK_BYTES):
                        stream.write(chunk)
                        present += len(chunk)
                        if present >= next_log:
                            log(f"downloaded {present / expected_size:.0%}")
                            next_log += DOWNLOAD_LOG_EVERY_BYTES
            finally:
                response.release_conn()
        except ArchiveDownloadError:
            raise
        except (urllib3.exceptions.HTTPError, OSError) as error:
            log(f"download interrupted ({type(error).__name__}); attempt {attempt} of {DOWNLOAD_ATTEMPTS}")
            time.sleep(DOWNLOAD_RETRY_SECONDS)
    size = path.stat().st_size if path.exists() else 0
    if size != expected_size:
        raise ArchiveDownloadError(f"the archive has {size:,} bytes, expected {expected_size:,}")
    with path.open("rb") as stream:
        if stream.read(len(ZIP_SIGNATURE)) != ZIP_SIGNATURE:
            raise ArchiveDownloadError("the downloaded file is not a zip archive")


def upload_missing_photos(client: Minio, bucket: str, archive_path: Path, missing: Iterable[str]) -> int:
    """Upload the archive members whose NFC name is one of the missing keys.

    Args:
        client: MinIO client allowed to write the bucket.
        bucket: Bucket name.
        archive_path: The downloaded zip archive.
        missing: Object keys to upload.

    Returns:
        int: Number of objects uploaded.
    """
    wanted = set(missing)
    uploaded = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            key = unicodedata.normalize("NFC", info.filename)
            if info.is_dir() or key not in wanted:
                continue
            with archive.open(info) as stream:
                client.put_object(
                    bucket,
                    key,
                    stream,
                    length=info.file_size,
                    content_type=mimetypes.guess_type(key)[0] or "application/octet-stream",
                )
            uploaded += 1
            if uploaded % UPLOAD_LOG_EVERY == 0:
                log(f"uploaded {uploaded:,} of {len(wanted):,}")
    return uploaded


def sync_photos(client: Minio, bucket: str, manifest_keys: Set[str], archive_url: str, archive_size: int,
                work_dir: Path, http: urllib3.PoolManager) -> None:
    """Upload whatever the bucket lacks, downloading the archive only when something is missing.

    Raises:
        SystemExit: Keys of the manifest are still missing after the upload (the archive and
            the manifest disagree).
        ArchiveDownloadError: The archive could not be downloaded.
    """
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        log(f"created bucket {bucket}")
    missing = manifest_keys - existing_keys(client, bucket)
    if not missing:
        log(f"all {len(manifest_keys):,} photos are in bucket {bucket}; nothing to do")
        return
    log(f"{len(missing):,} of {len(manifest_keys):,} photos are missing from bucket {bucket}")
    archive_path = work_dir / ARCHIVE_FILE_NAME
    download_archive(archive_url, archive_path, archive_size, http)
    uploaded = upload_missing_photos(client, bucket, archive_path, missing)
    still_missing = manifest_keys - existing_keys(client, bucket)
    if still_missing:
        example = sorted(still_missing)[0]
        raise SystemExit(
            f"[photos] {len(still_missing):,} photos of the manifest are not in the archive (e.g. {example!r}); "
            "the archive and data/gold/images_manifest.csv disagree"
        )
    archive_path.unlink()
    log(f"uploaded {uploaded:,} photos; all {len(manifest_keys):,} are in bucket {bucket}")


def main() -> None:
    """Read the settings, then sync the bucket with the manifest."""
    http = urllib3.PoolManager(
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        timeout=urllib3.Timeout(connect=10, read=120),
        retries=urllib3.Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504]),
    )
    client = Minio(
        required_setting("MINIO_END_POINT"),
        access_key=required_setting("MINIO_ACCESS_KEY_ID"),
        secret_key=required_setting("MINIO_SECRET_ACCESS_KEY"),
        secure=os.environ.get("MINIO_SECURE", "").strip().lower() in ("true", "1", "yes"),
    )
    sync_photos(
        client,
        required_setting("MINIO_BUCKET_NAME"),
        read_manifest_keys(DATA_DIR / "images_manifest.csv"),
        os.environ.get("PHOTO_ARCHIVE_URL", "").strip() or DEFAULT_ARCHIVE_URL,
        int(os.environ.get("PHOTO_ARCHIVE_SIZE", "").strip() or DEFAULT_ARCHIVE_SIZE),
        Path(os.environ.get("PHOTO_WORK_DIR", "").strip() or "/tmp"),
        http,
    )


if __name__ == "__main__":
    main()
