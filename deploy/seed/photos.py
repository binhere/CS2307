"""Make the MinIO bucket hold every listing photo the catalog references.

Runs from the seed image as its own CronJob (command: python /seed/photos.py). Safe to run any
number of times:
- Lists the bucket's images/ prefix (one request per 1,000 objects) and compares it with
  images_manifest.csv. Nothing missing: exits.
- Photos missing: reads the archive's table of contents from the URL (range requests), then
  uploads exactly the missing members under their manifest key. Many missing: the archive is
  downloaded once (resuming a partial download) and deleted afterwards; a few: each is read from
  the URL. Objects that are not in the manifest are left alone.
- Every photo is checked against the archive's CRC-32 when read and again after upload by
  reading the stored copy back; a mismatch (a flipped bit anywhere on the way) is retried from
  fresh bytes, up to UPLOAD_ATTEMPTS times. Photos that still fail are reported at the end, after
  all others are uploaded.

Input: environment variables MINIO_END_POINT (host:port), MINIO_ACCESS_KEY_ID,
    MINIO_SECRET_ACCESS_KEY (a key that may write the bucket), MINIO_BUCKET_NAME and
    MINIO_SECURE ("true" for https); optional PHOTO_ARCHIVE_URL and PHOTO_ARCHIVE_SIZE (default:
    the archive the manifest was made from), PHOTO_WORK_DIR (default /tmp; needs room for the
    archive, about 9 GB), PHOTO_VERIFY_EXISTING ("true": also read back every stored photo and
    replace the damaged ones) and SEED_DATA_DIR (default /seed/data, holding images_manifest.csv).
Output: exit code 0 when every manifest key is in the bucket and intact; non-zero otherwise, so
    the Job retries. Progress goes to stdout.
"""

from __future__ import annotations

import csv
import io
import mimetypes
import os
import re
import struct
import time
import unicodedata
import zipfile
import zlib
from pathlib import Path
from typing import Callable, Dict, Optional, Set

import certifi
import urllib3
from minio import Minio
from minio.error import S3Error

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
# Tries per photo: read, check, upload, read back. A flipped bit fails one attempt, rarely two.
UPLOAD_ATTEMPTS = 3
# Above this many photos to upload, one sequential download beats a pair of range requests per photo.
DOWNLOAD_THRESHOLD = 1000
CONTENT_RANGE = re.compile(r"bytes (\d+)-\d+/(\d+)")
# A zip local file header: signature, version, flags, method, time, date, CRC-32, compressed size,
# size, name length, extra field length (30 bytes).
LOCAL_HEADER = struct.Struct("<4s5H3L2H")


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


class PhotoIntegrityError(RuntimeError):
    """A photo could not be read, or stored, with the content its archive CRC-32 promises."""


class RemoteArchive:
    """A read-only, seekable view of the archive over HTTP range requests.

    zipfile reads the end record and the central directory through it (a few requests for the
    ~5 MB directory), so the CRC-32, size and offset of every member are known without the
    download. It implements only what zipfile.ZipFile needs: read, seek, tell, seekable.
    """

    def __init__(self, url: str, http: urllib3.PoolManager):
        self.url = url
        self.http = http
        self.position = 0
        response = http.request("GET", url, headers={"Range": "bytes=0-0"}, redirect=True)
        match = CONTENT_RANGE.match(response.headers.get("Content-Range", ""))
        if response.status != 206 or match is None:
            raise ArchiveDownloadError(f"the archive URL does not answer range requests (HTTP {response.status})")
        self.size = int(match.group(2))

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        base = {os.SEEK_SET: 0, os.SEEK_CUR: self.position, os.SEEK_END: self.size}[whence]
        self.position = base + offset
        return self.position

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.size - self.position
        size = min(size, self.size - self.position)
        if size <= 0:
            return b""
        data = read_remote_range(self.http, self.url, self.position, size)
        self.position += len(data)
        return data


def read_remote_range(http: urllib3.PoolManager, url: str, offset: int, length: int) -> bytes:
    """Read length bytes at offset from the archive URL.

    Raises:
        ArchiveDownloadError: The server did not answer with exactly that range.
    """
    response = http.request("GET", url, headers={"Range": f"bytes={offset}-{offset + length - 1}"}, redirect=True)
    if response.status != 206 or len(response.data) != length:
        raise ArchiveDownloadError(f"range {offset}+{length}: HTTP {response.status}, {len(response.data)} bytes")
    return response.data


def remote_members(url: str, http: urllib3.PoolManager) -> Dict[str, zipfile.ZipInfo]:
    """Read the archive's table of contents from the URL, without downloading the archive.

    Returns:
        dict: Object key (the member name in NFC) to its ZipInfo (CRC-32, sizes, header offset).
    """
    with zipfile.ZipFile(RemoteArchive(url, http)) as archive:  # type: ignore[arg-type]
        return {unicodedata.normalize("NFC", info.filename): info for info in archive.infolist() if not info.is_dir()}


def member_content(read_bytes: Callable[[int, int], bytes], info: zipfile.ZipInfo) -> bytes:
    """Extract one member from raw archive bytes and prove it intact against the CRC-32.

    The offsets and the CRC come from the remote table of contents, so a flipped bit in a local
    copy of the archive's own directory cannot misdirect the read.

    Args:
        read_bytes: Returns length bytes at an offset of the archive (a local file or the URL).
        info: The member's entry from remote_members().

    Returns:
        bytes: The member's content.

    Raises:
        PhotoIntegrityError: The local header is not where the directory says, the member uses an
            unsupported compression, or the content does not match its size and CRC-32.
    """
    header = read_bytes(info.header_offset, LOCAL_HEADER.size)
    signature, *_, name_length, extra_length = LOCAL_HEADER.unpack(header)
    if signature != ZIP_SIGNATURE:
        raise PhotoIntegrityError(f"no local header at offset {info.header_offset}")
    compressed = read_bytes(info.header_offset + LOCAL_HEADER.size + name_length + extra_length, info.compress_size)
    try:
        if info.compress_type == zipfile.ZIP_STORED:
            content = compressed
        elif info.compress_type == zipfile.ZIP_DEFLATED:
            content = zlib.decompress(compressed, -zlib.MAX_WBITS)
        else:
            raise PhotoIntegrityError(f"unsupported compression method {info.compress_type}")
    except zlib.error as error:
        raise PhotoIntegrityError(f"cannot inflate: {error}") from error
    if len(content) != info.file_size or zlib.crc32(content) != info.CRC:
        raise PhotoIntegrityError("content does not match its CRC-32")
    return content


def stored_crc(client: Minio, bucket: str, key: str) -> int:
    """Read an object back from the bucket and return its CRC-32."""
    response = client.get_object(bucket, key)
    try:
        return zlib.crc32(response.read())
    finally:
        response.close()
        response.release_conn()


def is_stored_intact(client: Minio, bucket: str, key: str, expected_crc: int) -> bool:
    """Whether the stored copy of a photo reads back completely and matches its CRC-32.

    A stored copy that cannot be read counts as damaged: Garage breaks off the response when a
    block on its disk no longer matches the block's hash, so the client sees a cut-off read.
    """
    try:
        return stored_crc(client, bucket, key) == expected_crc
    except (S3Error, urllib3.exceptions.HTTPError, OSError) as error:
        log(f"cannot read the stored copy of {key!r} ({type(error).__name__}); counting it as damaged")
        return False


def upload_verified(client: Minio, bucket: str, key: str, info: zipfile.ZipInfo,
                    read_local: Optional[Callable[[int, int], bytes]], read_remote: Callable[[int, int], bytes]) -> int:
    """Upload one photo and read it back, retrying until the stored copy matches the archive's CRC-32.

    Each attempt reads the photo again (the local archive first, then straight from the URL),
    checks its CRC-32, uploads it and compares the stored copy's CRC-32. A bit flipped anywhere
    on the way fails that attempt, and the next one starts from fresh bytes.

    Returns:
        int: The number of attempts it took (1 when nothing went wrong).

    Raises:
        PhotoIntegrityError: No attempt of UPLOAD_ATTEMPTS produced an intact stored copy; the
            message lists each attempt's error. The damaged copy is removed first, so the next
            run sees the photo as missing instead of present.
    """
    errors = []
    for attempt in range(UPLOAD_ATTEMPTS):
        source = read_local if attempt == 0 and read_local is not None else read_remote
        try:
            content = member_content(source, info)
            client.put_object(bucket, key, io.BytesIO(content), length=len(content),
                              content_type=mimetypes.guess_type(key)[0] or "application/octet-stream")
            if stored_crc(client, bucket, key) == info.CRC:
                return attempt + 1
            raise PhotoIntegrityError("the stored copy does not match its CRC-32")
        except (PhotoIntegrityError, ArchiveDownloadError, S3Error, urllib3.exceptions.HTTPError, OSError) as error:
            errors.append(f"{'local' if source is read_local else 'remote'}: {error}")
    try:
        client.remove_object(bucket, key)
    except (S3Error, urllib3.exceptions.HTTPError, OSError) as error:
        errors.append(f"removing the damaged copy failed: {error}")
    raise PhotoIntegrityError("; ".join(errors))


def sync_photos(client: Minio, bucket: str, manifest_keys: Set[str], archive_url: str, archive_size: int,
                work_dir: Path, http: urllib3.PoolManager, verify_existing: bool = False,
                download_threshold: int = DOWNLOAD_THRESHOLD) -> None:
    """Upload whatever the bucket lacks, each photo verified against its CRC-32.

    Args:
        client: MinIO client allowed to write the bucket.
        bucket: Bucket name.
        manifest_keys: Every object key the catalog references.
        archive_url: Direct download URL of the archive; it must honour range requests.
        archive_size: The archive's exact size in bytes.
        work_dir: Where the archive is downloaded when many photos are missing.
        http: Pool for the archive requests.
        verify_existing: Also read back every photo already in the bucket and replace the ones
            whose CRC-32 differs (about 9.5 GB of reads; for after a hardware fault).
        download_threshold: Above this many photos to upload, the whole archive is downloaded once;
            at or below it, each photo is read from the URL with range requests.

    Raises:
        SystemExit: Manifest keys are not in the archive, or some photos could not be stored
            intact; every other photo is uploaded first.
        ArchiveDownloadError: The archive's table of contents or the archive itself could not be read.
    """
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        log(f"created bucket {bucket}")
    present = existing_keys(client, bucket) & manifest_keys
    to_upload = manifest_keys - present
    if not to_upload and not verify_existing:
        log(f"all {len(manifest_keys):,} photos are in bucket {bucket}; nothing to do")
        return
    members = remote_members(archive_url, http)
    absent = manifest_keys - members.keys()
    if absent:
        raise SystemExit(f"[photos] {len(absent):,} photos of the manifest are not in the archive "
                         f"(e.g. {sorted(absent)[0]!r}); the archive and data/gold/images_manifest.csv disagree")
    if verify_existing:
        damaged = set()
        for count, key in enumerate(sorted(present), start=1):
            if not is_stored_intact(client, bucket, key, members[key].CRC):
                damaged.add(key)
            if count % UPLOAD_LOG_EVERY == 0:
                log(f"verified {count:,} of {len(present):,} stored photos; {len(damaged):,} damaged so far")
        log(f"verified {len(present):,} stored photos: {len(damaged):,} differ from the archive"
            + (f" (e.g. {sorted(damaged)[:3]})" if damaged else ""))
        to_upload |= damaged
    if not to_upload:
        log(f"all {len(manifest_keys):,} photos are in bucket {bucket} and intact; nothing to do")
        return
    log(f"{len(to_upload):,} of {len(manifest_keys):,} photos to upload to bucket {bucket}")

    archive_path = work_dir / ARCHIVE_FILE_NAME
    local_archive = None
    if len(to_upload) > download_threshold:
        download_archive(archive_url, archive_path, archive_size, http)
        local_archive = archive_path.open("rb")

    def read_local(offset: int, length: int) -> bytes:
        local_archive.seek(offset)
        return local_archive.read(length)

    def read_remote(offset: int, length: int) -> bytes:
        return read_remote_range(http, archive_url, offset, length)

    failures = {}
    retried = 0
    try:
        # Archive order keeps the reads of the local copy sequential.
        for count, key in enumerate(sorted(to_upload, key=lambda name: members[name].header_offset), start=1):
            try:
                attempts = upload_verified(client, bucket, key, members[key],
                                           read_local if local_archive else None, read_remote)
                if attempts > 1:
                    retried += 1
                    log(f"{key!r} needed {attempts} attempts")
            except PhotoIntegrityError as error:
                failures[key] = str(error)
                log(f"could not store {key!r} intact: {error}")
            if count % UPLOAD_LOG_EVERY == 0:
                log(f"uploaded {count - len(failures):,} of {len(to_upload):,}")
    finally:
        if local_archive is not None:
            local_archive.close()
    if local_archive is not None:
        archive_path.unlink()
    log(f"{retried:,} of {len(to_upload):,} photos needed more than one attempt")
    if failures:
        raise SystemExit(f"[photos] {len(failures):,} of {len(to_upload):,} photos could not be stored intact "
                         f"after {UPLOAD_ATTEMPTS} attempts each (e.g. {sorted(failures)[0]!r}); the rest are uploaded")
    log(f"uploaded {len(to_upload):,} photos, each verified; all {len(manifest_keys):,} are in bucket {bucket}")


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
        verify_existing=os.environ.get("PHOTO_VERIFY_EXISTING", "").strip().lower() in ("true", "1", "yes"),
    )


if __name__ == "__main__":
    main()
