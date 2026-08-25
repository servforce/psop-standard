from __future__ import annotations

from fastapi import APIRouter

from app.core.standard_config import standard_settings as settings

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
def get_runtime_config():
    return {
        "standard_update_scheduler_enabled": settings.standard_update_scheduler_enabled,
        "standard_update_national_enabled": settings.standard_update_national_enabled,
        "standard_update_industry_enabled": settings.standard_update_industry_enabled,
        "standard_update_local_enabled": settings.standard_update_local_enabled,
        "standard_update_industry_categories": list(settings.standard_update_industry_categories),
        "standard_update_local_categories": list(settings.standard_update_local_categories),
        "standard_update_sacinfo_require_categories": settings.standard_update_sacinfo_require_categories,
    }
