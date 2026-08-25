from __future__ import annotations

from dataclasses import dataclass

from app.core.env import env, env_bool, load_dotenv


@dataclass(frozen=True)
class StorageSettings:
    storage_backend: str = "minio"
    object_store_endpoint: str = "http://10.0.0.20:9000"
    object_store_access_key: str = "minioadmin"
    object_store_secret_key: str = "minioadmin"
    object_store_bucket: str = "servforce-materials"
    object_store_region: str = "us-east-1"
    object_store_secure: bool = False

    @classmethod
    def from_env(cls) -> "StorageSettings":
        load_dotenv()
        return cls(
            storage_backend=env("STORAGE_BACKEND", "minio").lower(),
            object_store_endpoint=env("OBJECT_STORE_ENDPOINT", "http://10.0.0.20:9000"),
            object_store_access_key=env("OBJECT_STORE_ACCESS_KEY", "minioadmin"),
            object_store_secret_key=env("OBJECT_STORE_SECRET_KEY", "minioadmin"),
            object_store_bucket=env("OBJECT_STORE_BUCKET", "servforce-materials"),
            object_store_region=env("OBJECT_STORE_REGION", "us-east-1"),
            object_store_secure=env_bool("OBJECT_STORE_SECURE", False),
        )


storage_settings = StorageSettings.from_env()
