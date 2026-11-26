"""Read and write property images in the private MinIO bucket.

MinIO is optional: a deployment without images leaves MINIO_END_POINT empty, and the
frontend then shows its illustration for every listing. Writes come from listings users post.
"""

import io
from typing import List, Optional
from urllib.parse import unquote, urlparse

import certifi
import urllib3
from loguru import logger
from minio import Minio

# The SDK's own default waits 5 minutes to connect and retries 5 times, so an
# unreachable MinIO would hold a worker thread for minutes per image. Images are
# optional, so fail within seconds and let the frontend show its fallback.
CONNECT_TIMEOUT_SECONDS = 2
READ_TIMEOUT_SECONDS = 15
RETRY_COUNT = 1


class ImageStorageUnavailableError(RuntimeError):
    """MinIO is not configured for this deployment."""


def build_http_client() -> urllib3.PoolManager:
    """Create the connection pool MinIO requests go through.

    Returns:
        urllib3.PoolManager: Short connect/read timeouts and a single retry with backoff
        for 5xx answers, in place of the SDK's minutes-long defaults.
    """
    return urllib3.PoolManager(
        timeout=urllib3.util.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS),
        maxsize=10,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        retries=urllib3.Retry(total=RETRY_COUNT, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]),
    )


class MinioClient:
    def __init__(self, config: dict):
        self._config = config
        self.minio_url = config.get("minio_end_point")
        self.access_key = config.get("minio_access_key_id")
        self.secret_key = config.get("minio_secret_access_key")
        self.minio_bucket_name = config.get("minio_bucket_name")
        self.secure = True if config.get("minio_secure") in ["True", "True", "TRUE", '1', 1] else False
        self._minio_client: Optional[Minio] = None

    @property
    def is_configured(self) -> bool:
        """Whether this deployment has an image store (MINIO_END_POINT is set)."""
        return bool(self.minio_url and str(self.minio_url).strip())

    def start(self) -> None:
        """Create the SDK client, or leave images disabled when MinIO is not configured."""
        if not self.is_configured:
            logger.warning("MINIO_END_POINT is not set; property images are unavailable")
            return
        self._minio_client = Minio(
            str(self.minio_url).strip(),
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
            http_client=build_http_client(),
        )

    def normalize_object_key(self, image_path: str) -> str:
        """Return a safe MinIO object key from a DB URL or object path."""
        raw_path = str(image_path or "").strip()
        if not raw_path:
            raise ValueError("Image path is required")

        parsed = urlparse(raw_path)
        path = unquote(parsed.path if parsed.scheme else raw_path).lstrip("/")
        bucket_prefix = f"{self.minio_bucket_name}/"
        if path.startswith(bucket_prefix):
            path = path[len(bucket_prefix):]

        parts = path.split("/")
        if not path.startswith("images/") or any(
            part in {"", ".", ".."} for part in parts
        ):
            raise ValueError("Invalid property image path")
        return path

    def minio_get_object(self, image_path: str):
        """Open an image stored in the configured private property bucket.

        Args:
            image_path: Object key ("images/...") or a URL whose path holds one.

        Returns:
            urllib3.BaseHTTPResponse: The object's streaming response.

        Raises:
            ValueError: The path is not a property image key.
            ImageStorageUnavailableError: MinIO is not configured or not started.
            minio.S3Error: MinIO answered with an error (e.g. NoSuchKey).
            urllib3.exceptions.HTTPError: MinIO could not be reached in time.
        """
        object_key = self.normalize_object_key(image_path)
        if self._minio_client is None:
            raise ImageStorageUnavailableError("MinIO is not configured")
        return self._minio_client.get_object(
            bucket_name=self.minio_bucket_name,
            object_name=object_key,
        )

    def put_image(self, object_key: str, content: bytes, content_type: str) -> None:
        """Store an image under a property image key, replacing any object already there.

        Args:
            object_key: Key under "images/", e.g. "images/user-listings/9000000001/gallery/001.jpg".
            content: The image bytes.
            content_type: MIME type served back with the image, e.g. "image/jpeg".

        Raises:
            ValueError: The key is not a property image key.
            ImageStorageUnavailableError: MinIO is not configured or not started.
            minio.S3Error: MinIO answered with an error.
        """
        object_key = self.normalize_object_key(object_key)
        if self._minio_client is None:
            raise ImageStorageUnavailableError("MinIO is not configured")
        self._minio_client.put_object(self.minio_bucket_name, object_key, io.BytesIO(content),
                                      length=len(content), content_type=content_type)

    def remove_images(self, object_keys: List[str]) -> None:
        """Remove images, ignoring failures: used to clean up after a listing could not be saved.

        Args:
            object_keys: Keys under "images/".
        """
        if self._minio_client is None:
            return
        for object_key in object_keys:
            try:
                self._minio_client.remove_object(self.minio_bucket_name, self.normalize_object_key(object_key))
            except Exception as error:
                logger.warning("Could not remove image {}: {}", object_key, type(error).__name__)

    def count_objects(self, prefix: str) -> Optional[int]:
        """Count the objects under a key prefix, for the data dashboard.

        Args:
            prefix: Key prefix, e.g. "images/".

        Returns:
            int | None: the number of objects, or None when no image store is configured or
            listing fails (logged as a warning). Lists 1,000 keys per request.
        """
        if self._minio_client is None:
            return None
        try:
            return sum(1 for _ in self._minio_client.list_objects(self.minio_bucket_name, prefix=prefix, recursive=True))
        except Exception as error:
            logger.warning("Image store listing unavailable: {}", type(error).__name__)
            return None

    def stop(self):
        self._minio_client = None
