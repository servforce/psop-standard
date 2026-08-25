from __future__ import annotations

import os
from pathlib import Path


DEFAULT_MODEL_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com"


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name, "true" if default else "false").lower()
    return value not in {"", "0", "false", "no", "off"}


def env_list(name: str, default: str = "") -> tuple[str, ...]:
    import re

    value = env(name, default)
    return tuple(part.strip() for part in re.split(r"[,;锛岋紱\n]+", value) if part.strip())
