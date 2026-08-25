from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.core.storage_config import StorageSettings, storage_settings


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    object_key: str
    media_type: str
    size_bytes: int
    checksum: str


class StorageService:
    def __init__(self, settings_: StorageSettings = storage_settings) -> None:
        self.settings = settings_
        self._client = None

    def upload_bytes(self, *, object_key: str, content: bytes, media_type: str, bucket: str | None = None) -> StoredObject:
        checksum = hashlib.sha256(content).hexdigest()
        self._ensure_minio_backend()
        target_bucket = bucket or self.settings.object_store_bucket
        self._put_minio(
            bucket=target_bucket,
            object_key=object_key,
            content=content,
            media_type=media_type,
            checksum=checksum,
        )
        return StoredObject(
            bucket=target_bucket,
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(content),
            checksum=checksum,
        )

    def upload_file(self, *, object_key: str, path: Path, media_type: str, bucket: str | None = None) -> StoredObject:
        checksum = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as file:
            while True:
                chunk = file.read(1024 * 1024)
                if not chunk:
                    break
                checksum.update(chunk)
                size_bytes += len(chunk)
        self._ensure_minio_backend()
        target_bucket = bucket or self.settings.object_store_bucket
        self._put_file(
            bucket=target_bucket,
            object_key=object_key,
            path=path,
            media_type=media_type,
            checksum=checksum.hexdigest(),
        )
        return StoredObject(
            bucket=target_bucket,
            object_key=object_key,
            media_type=media_type,
            size_bytes=size_bytes,
            checksum=checksum.hexdigest(),
        )

    def download_file(self, *, bucket: str, object_key: str, path: Path) -> None:
        self._ensure_minio_backend()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._get_client().download_file(bucket, object_key, str(path))

    def get_bytes(self, *, bucket: str, object_key: str) -> bytes:
        self._ensure_minio_backend()
        response = self._get_client().get_object(Bucket=bucket, Key=object_key)
        body = response.get("Body")
        if body is None:
            return b""
        try:
            return body.read()
        finally:
            body.close()

    def url_for(self, object_key: str) -> str:
        return f"/api/objects/{object_key}"

    def _ensure_minio_backend(self) -> None:
        if self.settings.storage_backend != "minio":
            raise ValueError("Only STORAGE_BACKEND=minio is supported.")

    def _put_minio(self, *, bucket: str, object_key: str, content: bytes, media_type: str, checksum: str) -> None:
        client = self._get_client()
        self._ensure_bucket(client, bucket)
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=content,
            ContentType=media_type,
            Metadata={"sha256": checksum},
        )

    def _put_file(self, *, bucket: str, object_key: str, path: Path, media_type: str, checksum: str) -> None:
        client = self._get_client()
        self._ensure_bucket(client, bucket)
        with path.open("rb") as file:
            client.put_object(
                Bucket=bucket,
                Key=object_key,
                Body=file,
                ContentType=media_type,
                Metadata={"sha256": checksum},
            )

    def _get_client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.object_store_endpoint,
                aws_access_key_id=self.settings.object_store_access_key,
                aws_secret_access_key=self.settings.object_store_secret_key,
                region_name=self.settings.object_store_region,
                use_ssl=self.settings.object_store_secure,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    proxies={},
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"max_attempts": 2},
                ),
            )
        return self._client

    def _ensure_bucket(self, client, bucket: str) -> None:
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)


storage_service = StorageService()
