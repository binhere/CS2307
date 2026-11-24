from urllib.parse import unquote, urlparse

from minio import Minio


class MinioClient:
    def __init__(self, config: dict):
        self._config = config
        self.minio_url = config.get("minio_end_point")
        self.access_key = config.get("minio_access_key_id")
        self.secret_key = config.get("minio_secret_access_key")
        self.minio_bucket_name = config.get("minio_bucket_name")
        self.secure = True if config.get("minio_secure") in ["True", "True", "TRUE", '1', 1] else False
        self._minio_client = None

    def start(self):
        self._minio_client = Minio(
            self.minio_url,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure
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
        """Open an image stored in the configured private property bucket."""
        if self._minio_client is None:
            raise RuntimeError("MinIO client is not started")
        object_key = self.normalize_object_key(image_path)
        return self._minio_client.get_object(
            bucket_name=self.minio_bucket_name,
            object_name=object_key,
        )

    def stop(self):
        self._minio_client = None
