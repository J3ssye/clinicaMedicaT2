from __future__ import annotations

from io import BytesIO

from minio import Minio

from app.core.config import get_settings


settings = get_settings()


class StorageService:
    def __init__(self) -> None:
        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(settings.minio_bucket):
            self.client.make_bucket(settings.minio_bucket)

    def upload_bytes(self, object_key: str, content: bytes, content_type: str) -> None:
        self.ensure_bucket()
        self.client.put_object(
            settings.minio_bucket,
            object_key,
            data=BytesIO(content),
            length=len(content),
            content_type=content_type,
        )
