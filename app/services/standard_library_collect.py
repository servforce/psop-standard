from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.db.standard_library import StandardLibrarySessionLocal
from app.models.standard_library import (
    StandardLibraryStandard,
    StandardSource,
    StandardSyncItem,
    StandardSyncJob,
)


OPENSTD_SOURCE_SITE = "openstd.samr.gov.cn"
SACINFO_INDUSTRY_SOURCE_SITE = "hbba.sacinfo.org.cn"
SACINFO_LOCAL_SOURCE_SITE = "dbba.sacinfo.org.cn"
STANDARD_LIBRARY_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "octopus-standard-library")

SOURCE_LABELS = {
    "national": "National standards",
    "industry": "Industry standards",
    "local": "Local standards",
}

CATEGORY_LABELS = {
    "mandatory": "Mandatory national standard",
    "recommended": "Recommended national standard",
    "guidance": "Guidance national standard",
    "national": "National standard",
}

SOURCE_ENTRY_URLS = {
    "industry": "https://hbba.sacinfo.org.cn/",
    "local": "https://dbba.sacinfo.org.cn/",
}

SOURCE_SITES = {
    "national": OPENSTD_SOURCE_SITE,
    "industry": SACINFO_INDUSTRY_SOURCE_SITE,
    "local": SACINFO_LOCAL_SOURCE_SITE,
}


def mirror_standard_library_job(
    legacy_job: Any,
    *,
    job_type: str,
    trigger_type: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    error_message: str | None = None,
) -> uuid.UUID:
    with StandardLibrarySessionLocal() as session:
        job = upsert_standard_library_job(
            session,
            legacy_job,
            job_type=job_type,
            trigger_type=trigger_type,
            status=status,
            stage=stage,
            error_message=error_message,
        )
        session.commit()
        return job.id


def upsert_standard_library_job(
    session: Session,
    legacy_job: Any,
    *,
    job_type: str,
    source: str = "national",
    trigger_type: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    error_message: str | None = None,
    entry_url: str = "",
) -> StandardSyncJob:
    source = normalize_source(source)
    now = utcnow()
    legacy_id = clean_text(item_value(legacy_job, "id")) or uuid.uuid4().hex
    job_id = deterministic_uuid(f"sync-job:{job_type}:{source}:{legacy_id}")
    job = session.get(StandardSyncJob, job_id)
    if job is None:
        job = StandardSyncJob(
            id=job_id,
            job_type=job_type,
            source=source,
            trigger_type=normalize_trigger_type(trigger_type or item_value(legacy_job, "trigger_type") or "system"),
            status="pending",
            stage="starting",
            created_at=coerce_datetime(item_value(legacy_job, "created_at")) or now,
        )
        session.add(job)

    job.trigger_type = normalize_trigger_type(trigger_type or item_value(legacy_job, "trigger_type") or job.trigger_type)
    job.status = normalize_job_status(status or item_value(legacy_job, "status") or job.status)
    job.stage = clean_text(stage or item_value(legacy_job, "stage") or job.stage)
    job.progress_percent = progress_percent_for_status(job.status)
    job.started_at = coerce_datetime(item_value(legacy_job, "started_at")) or job.started_at
    job.finished_at = (
        coerce_datetime(item_value(legacy_job, "finished_at"))
        or coerce_datetime(item_value(legacy_job, "completed_at"))
        or job.finished_at
    )
    if job.started_at and job.finished_at:
        job.duration_ms = max(0, int((job.finished_at - job.started_at).total_seconds() * 1000))
    job.heartbeat_at = now if job.status == "running" else job.heartbeat_at
    job.scanned_pages = int_value(item_value(legacy_job, "scanned_pages") or item_value(legacy_job, "total_pages"))
    job.discovered_count = int_value(
        item_value(legacy_job, "discovered_count")
        or item_value(legacy_job, "total_discovered")
        or item_value(legacy_job, "scanned_count")
    )
    job.processed_count = max(
        int_value(item_value(legacy_job, "processed_count")),
        int_value(item_value(legacy_job, "new_count"))
        + int_value(item_value(legacy_job, "updated_count"))
        + int_value(item_value(legacy_job, "unchanged_count"))
        + int_value(item_value(legacy_job, "skipped_count"))
        + int_value(item_value(legacy_job, "failed_count")),
    )
    job.need_download_count = int_value(item_value(legacy_job, "total_downloadable") or item_value(legacy_job, "new_count"))
    job.downloaded_count = int_value(item_value(legacy_job, "downloaded_count") or item_value(legacy_job, "uploaded_count"))
    job.download_failed_count = int_value(item_value(legacy_job, "download_failed_count"))
    job.new_active_count = int_value(item_value(legacy_job, "new_active_count") or item_value(legacy_job, "new_count"))
    job.expired_count = int_value(item_value(legacy_job, "expired_count"))
    job.failed_count = int_value(item_value(legacy_job, "failed_count"))
    job.error_message = clean_text(error_message if error_message is not None else item_value(legacy_job, "error_message")) or None
    job.updated_at = now
    session.add(job)
    session.flush([job])
    upsert_standard_source(session, job=job, entry_url=entry_url)
    return job


def upsert_national_standard_from_raw(
    raw: Any,
    *,
    detail: dict[str, Any] | None = None,
    legacy_standard_id: str = "",
    bucket: str = "",
    object_key: str = "",
    checksum: str = "",
    size_bytes: int | None = None,
    fingerprint: str = "",
    file_access_type: str = "",
) -> uuid.UUID:
    with StandardLibrarySessionLocal() as session:
        standard = upsert_standard_from_raw(
            session,
            raw,
            source="national",
            detail=detail,
            legacy_standard_id=legacy_standard_id,
            bucket=bucket,
            object_key=object_key,
            checksum=checksum,
            size_bytes=size_bytes,
            fingerprint=fingerprint,
            file_access_type=file_access_type,
        )
        session.commit()
        return standard.id


def upsert_collected_standard(
    raw: Any,
    *,
    source: str,
    detail: dict[str, Any] | None = None,
    legacy_standard_id: str = "",
    bucket: str = "",
    object_key: str = "",
    checksum: str = "",
    size_bytes: int | None = None,
    fingerprint: str = "",
    file_access_type: str = "",
    category: str = "",
    category_label: str = "",
    source_label: str = "",
    source_site: str = "",
    external_id: str = "",
    detail_url: str = "",
    pdf_url: str = "",
    online_url: str = "",
) -> uuid.UUID:
    with StandardLibrarySessionLocal() as session:
        standard = upsert_standard_from_raw(
            session,
            raw,
            source=source,
            detail=detail,
            legacy_standard_id=legacy_standard_id,
            bucket=bucket,
            object_key=object_key,
            checksum=checksum,
            size_bytes=size_bytes,
            fingerprint=fingerprint,
            file_access_type=file_access_type,
            category=category,
            category_label=category_label,
            source_label=source_label,
            source_site=source_site,
            external_id=external_id,
            detail_url=detail_url,
            pdf_url=pdf_url,
            online_url=online_url,
        )
        session.commit()
        return standard.id


def upsert_standard_from_raw(
    session: Session,
    raw: Any,
    *,
    source: str = "national",
    detail: dict[str, Any] | None = None,
    legacy_standard_id: str = "",
    bucket: str = "",
    object_key: str = "",
    checksum: str = "",
    size_bytes: int | None = None,
    fingerprint: str = "",
    file_access_type: str = "",
    category: str = "",
    category_label: str = "",
    source_label: str = "",
    source_site: str = "",
    external_id: str = "",
    detail_url: str = "",
    pdf_url: str = "",
    online_url: str = "",
) -> StandardLibraryStandard:
    source = normalize_source(source)
    detail = detail or {}
    now = utcnow()
    code = clean_text(item_value(raw, "standard_code") or item_value(raw, "code"))
    name = clean_text(item_value(raw, "standard_name") or item_value(raw, "name") or item_value(raw, "chName")) or code
    detail_url = clean_text(detail_url or detail.get("detail_url") or item_value(raw, "detail_url"))
    external_id = (
        clean_text(external_id)
        or external_id_from_detail_url(detail_url)
        or clean_text(item_value(raw, "external_id") or item_value(raw, "pk"))
    )
    category = resolve_category(source=source, code=code, raw=raw, category=category)
    code_normalized = normalize_code(code or external_id or legacy_standard_id or name)
    standard_id = deterministic_uuid(f"standard:{source}:{external_id or code_normalized}:{category}")

    standard = find_standard(session, source=source, code_normalized=code_normalized, category=category, external_id=external_id)
    if standard is None:
        standard = session.get(StandardLibraryStandard, standard_id)
    is_new = standard is None
    if standard is None:
        standard = StandardLibraryStandard(
            id=standard_id,
            code=code or external_id or str(standard_id),
            code_normalized=code_normalized,
            name=name,
            source=source,
            source_label=clean_text(source_label or item_value(raw, "source_label")) or SOURCE_LABELS[source],
            category=category,
            official_status="current",
            file_access_type="unavailable",
            materialize_status="pending",
            index_status="pending",
            first_seen_at=now,
            created_at=now,
        )
        session.add(standard)

    raw_status = clean_text(detail.get("standard_status") or item_value(raw, "standard_status") or item_value(raw, "source_status_raw"))
    publish_date = parse_standard_date(item_value(raw, "publish_date") or detail.get("publish_date"))
    effective_date = parse_standard_date(detail.get("effective_date") or item_value(raw, "effective_date"))
    resolved_file_access = file_access_type or ("downloadable" if bucket and object_key else standard.file_access_type or "unavailable")
    if resolved_file_access not in {"downloadable", "online_only", "unavailable"}:
        resolved_file_access = "unavailable"

    previous_file_fingerprint = standard.file_fingerprint
    standard.code = code or standard.code
    standard.code_normalized = code_normalized
    standard.name = name or standard.name
    standard.source = source
    standard.source_label = clean_text(source_label or item_value(raw, "source_label")) or standard.source_label or SOURCE_LABELS[source]
    standard.category = category
    standard.category_label = clean_text(category_label or item_value(raw, "category_label")) or CATEGORY_LABELS.get(category, category)
    standard.standard_org = clean_text(item_value(raw, "standard_org")) or standard_org_from_code(code)
    standard.official_status = official_status_from_raw(raw_status)
    standard.official_status_raw = raw_status or standard.official_status_raw
    standard.publish_date = publish_date or standard.publish_date
    standard.effective_date = effective_date or standard.effective_date
    standard.source_site = clean_text(source_site or item_value(raw, "source_site")) or SOURCE_SITES[source]
    standard.external_id = external_id or standard.external_id
    standard.detail_url = detail_url or standard.detail_url
    standard.pdf_url = clean_text(pdf_url or detail.get("download_url") or item_value(raw, "download_url")) or standard.pdf_url
    standard.online_url = clean_text(online_url or detail.get("online_url") or item_value(raw, "online_url")) or standard.online_url
    standard.file_access_type = resolved_file_access
    if bucket:
        standard.source_pdf_bucket = bucket
    if object_key:
        standard.source_pdf_object_key = object_key
    if checksum:
        standard.source_pdf_hash = checksum
    if size_bytes is not None:
        standard.source_pdf_size_bytes = int(size_bytes)
    standard.metadata_fingerprint = metadata_fingerprint(raw, detail=detail)
    standard.file_fingerprint = fingerprint or checksum or standard.file_fingerprint
    if is_new or resolved_file_access != "downloadable":
        standard.materialize_status = "pending" if resolved_file_access == "downloadable" else "skipped"
        standard.index_status = "pending" if resolved_file_access == "downloadable" else "skipped"
    elif checksum and checksum != previous_file_fingerprint:
        standard.materialize_status = "pending"
        standard.index_status = "pending"
        standard.materialize_error = None
        standard.index_error = None
    standard.last_seen_at = now
    standard.last_checked_at = now
    standard.updated_at = now
    session.add(standard)
    return standard


def mirror_standard_library_item(
    legacy_job: Any,
    raw: Any,
    *,
    job_type: str,
    legacy_standard_id: str = "",
    standard_id: uuid.UUID | str | None = None,
    metadata_action: str | None = None,
    status_change_type: str | None = None,
    file_decision: str | None = None,
    file_result: str | None = None,
    bucket: str = "",
    object_key: str = "",
    checksum: str = "",
    size_bytes: int | None = None,
    retry_count: int = 0,
    error_message: str = "",
    official_status_before: str = "",
    official_status_after: str = "",
) -> uuid.UUID:
    with StandardLibrarySessionLocal() as session:
        job = upsert_standard_library_job(session, legacy_job, job_type=job_type)
        item = upsert_standard_library_item(
            session,
            job=job,
            raw=raw,
            legacy_standard_id=legacy_standard_id,
            standard_id=standard_id,
            metadata_action=metadata_action,
            status_change_type=status_change_type,
            file_decision=file_decision,
            file_result=file_result,
            bucket=bucket,
            object_key=object_key,
            checksum=checksum,
            size_bytes=size_bytes,
            retry_count=retry_count,
            error_message=error_message,
            official_status_before=official_status_before,
            official_status_after=official_status_after,
        )
        session.commit()
        return item.id


def upsert_standard_library_item(
    session: Session,
    *,
    job: StandardSyncJob,
    raw: Any,
    source: str | None = None,
    legacy_standard_id: str = "",
    standard_id: uuid.UUID | str | None = None,
    metadata_action: str | None = None,
    status_change_type: str | None = None,
    file_decision: str | None = None,
    file_result: str | None = None,
    bucket: str = "",
    object_key: str = "",
    checksum: str = "",
    size_bytes: int | None = None,
    retry_count: int = 0,
    error_message: str = "",
    official_status_before: str = "",
    official_status_after: str = "",
) -> StandardSyncItem:
    source = normalize_source(source or job.source)
    now = utcnow()
    code = clean_text(item_value(raw, "standard_code") or item_value(raw, "code"))
    name = clean_text(item_value(raw, "standard_name") or item_value(raw, "name") or item_value(raw, "chName"))
    detail_url = clean_text(item_value(raw, "detail_url"))
    external_id = external_id_from_detail_url(detail_url) or clean_text(item_value(raw, "external_id") or item_value(raw, "pk"))
    category = resolve_category(source=source, code=code, raw=raw, category="")
    item_external_id = external_id or deterministic_item_external_id(code=code, name=name, detail_url=detail_url, legacy_standard_id=legacy_standard_id)

    statement = select(StandardSyncItem).where(
        StandardSyncItem.job_id == job.id,
        StandardSyncItem.source == source,
        StandardSyncItem.external_id == item_external_id,
    )
    item = session.scalars(statement).first()
    if item is None:
        item = StandardSyncItem(
            id=deterministic_uuid(f"sync-item:{job.id}:{source}:{item_external_id}"),
            job_id=job.id,
            source=source,
            external_id=item_external_id,
            created_at=now,
        )
        session.add(item)

    parsed_standard_id = parse_uuid(standard_id)
    if parsed_standard_id is None and (code or item_external_id):
        standard = find_standard(
            session,
            source=source,
            code_normalized=normalize_code(code),
            category=category,
            external_id=external_id,
        )
        parsed_standard_id = standard.id if standard is not None else None

    item.standard_id = parsed_standard_id
    item.code = code or item.code
    item.name = name or item.name
    item.category = category
    item.detail_url = detail_url or item.detail_url
    item.official_status_before = official_status_from_raw(official_status_before) if official_status_before else item.official_status_before
    item.official_status_after = official_status_from_raw(
        official_status_after or item_value(raw, "standard_status") or item_value(raw, "source_status_raw")
    )
    item.metadata_action = normalize_metadata_action(metadata_action)
    item.status_change_type = clean_text(status_change_type) or item.status_change_type
    item.file_decision = normalize_file_decision(file_decision)
    item.file_result = normalize_file_result(file_result)
    item.source_pdf_bucket = bucket or item.source_pdf_bucket
    item.source_pdf_object_key = object_key or item.source_pdf_object_key
    item.source_pdf_hash = checksum or item.source_pdf_hash
    if size_bytes is not None:
        item.source_pdf_size_bytes = int(size_bytes)
    item.online_url = clean_text(item_value(raw, "online_url")) or item.online_url
    item.retry_count = retry_count
    item.error_message = clean_text(error_message) or None
    item.updated_at = now
    session.add(item)
    return item


def standard_exists_in_library(*, code: str, detail_url: str = "", category: str = "", source: str = "national") -> bool:
    source = normalize_source(source)
    normalized = normalize_code(code)
    external_id = external_id_from_detail_url(detail_url)
    category = resolve_category(source=source, code=code, raw={}, category=category)
    with StandardLibrarySessionLocal() as session:
        return bool(find_standard(session, source=source, code_normalized=normalized, category=category, external_id=external_id))


def find_standard(
    session: Session,
    *,
    source: str,
    code_normalized: str,
    category: str,
    external_id: str = "",
) -> StandardLibraryStandard | None:
    conditions = [
        StandardLibraryStandard.source == source,
        StandardLibraryStandard.code_normalized == code_normalized,
        StandardLibraryStandard.category == category,
    ]
    if code_normalized:
        found = session.scalars(select(StandardLibraryStandard).where(*conditions).limit(1)).first()
        if found is not None:
            return found
    if external_id:
        return session.scalars(
            select(StandardLibraryStandard)
            .where(StandardLibraryStandard.source == source, StandardLibraryStandard.external_id == external_id)
            .limit(1)
        ).first()
    return None


def upsert_standard_source(session: Session, *, job: StandardSyncJob, entry_url: str = "") -> StandardSource:
    now = utcnow()
    source_key = normalize_source(job.source)
    source = session.scalars(select(StandardSource).where(StandardSource.source == source_key).limit(1)).first()
    if source is None:
        source = StandardSource(
            id=deterministic_uuid(f"source:{source_key}"),
            source=source_key,
            source_label=SOURCE_LABELS[source_key],
            entry_url=entry_url_for_source(source_key, entry_url=entry_url),
            created_at=now,
        )
        session.add(source)
    source.entry_url = entry_url_for_source(source_key, entry_url=entry_url)
    source.enabled = True
    source.scheduled_update_enabled = True
    source.historical_collect_enabled = True
    if job.job_type == "scheduled_update":
        source.last_update_job_id = job.id
    elif job.job_type == "historical_collect":
        source.last_historical_job_id = job.id
    if job.status == "completed":
        source.last_success_at = job.finished_at or job.updated_at or now
    source.updated_at = now
    return source


def normalize_source(value: str) -> str:
    source = clean_text(value).lower()
    if source in {"national", "industry", "local"}:
        return source
    raise ValueError(f"unsupported standard source: {value!r}")


def entry_url_for_source(source: str, *, entry_url: str = "") -> str:
    if entry_url:
        return entry_url
    if source == "national":
        return settings.openstd_source_url
    return SOURCE_ENTRY_URLS[source]


def resolve_category(*, source: str, code: str, raw: Any, category: str = "") -> str:
    explicit = clean_text(category or item_value(raw, "category"))
    if explicit:
        return explicit
    if source == "national":
        return national_standard_category_from_code(code)
    if source == "industry":
        return clean_text(item_value(raw, "industry") or item_value(raw, "source_scope")) or "industry"
    if source == "local":
        return clean_text(item_value(raw, "region") or item_value(raw, "source_scope") or item_value(raw, "industry")) or "local"
    return source


def normalize_code(value: str) -> str:
    return "".join(clean_text(value).upper().split())


def national_standard_category_from_code(standard_code: str) -> str:
    normalized = " ".join(clean_text(standard_code).upper().split())
    if normalized.startswith("GB/T"):
        return "recommended"
    if normalized.startswith("GB/Z"):
        return "guidance"
    if normalized.startswith("GB "):
        return "mandatory"
    return "national"


def standard_org_from_code(code: str) -> str:
    value = clean_text(code)
    return value.split(" ", 1)[0] if value else ""


def official_status_from_raw(raw_status: object) -> str:
    value = clean_text(raw_status)
    lowered = value.lower()
    if lowered in {"updated_available"} or "有更新版" in value:
        return "updated_available"
    if lowered in {"abolished", "scrapped", "scrap"}:
        return "abolished"
    if lowered in {"upcoming", "future"}:
        return "upcoming"
    if lowered in {"active", "current", "valid"}:
        return "current"
    if "abolish" in lowered or "scrap" in lowered or "废止" in value:
        return "abolished"
    if "upcoming" in lowered or "即将" in value:
        return "upcoming"
    if "active" in lowered or "current" in lowered or "现行" in value:
        return "current"
    return "current"


def normalize_job_status(value: object) -> str:
    status = clean_text(value).lower()
    if status in {"queued", "pending"}:
        return "pending"
    if status in {"running", "processing"}:
        return "running"
    if status in {"completed", "completed_with_errors", "success", "skipped_locked"}:
        return "completed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status == "failed":
        return "failed"
    return "pending"


def normalize_trigger_type(value: object) -> str:
    trigger = clean_text(value).lower()
    if trigger in {"scheduled", "schedule", "cron"}:
        return "schedule"
    if trigger in {"admin", "manual"}:
        return "admin"
    return "system"


def normalize_metadata_action(value: str | None) -> str | None:
    action = clean_text(value).lower()
    if action in {"new", "changed", "unchanged"}:
        return action
    return None


def normalize_file_decision(value: str | None) -> str | None:
    decision = clean_text(value).lower()
    if decision in {"download", "redownload", "no_download", "online_only", "unavailable", "skip"}:
        return decision
    return None


def normalize_file_result(value: str | None) -> str | None:
    result = clean_text(value).lower()
    if result in {"success", "failed", "skipped"}:
        return result
    return None


def parse_standard_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text_value = clean_text(value)
    if not text_value:
        return None
    try:
        return date.fromisoformat(text_value[:10])
    except ValueError:
        pass
    import re

    match = re.search(r"(\d{4})\D{0,4}(\d{1,2})\D{0,4}(\d{1,2})", text_value)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def metadata_fingerprint(raw: Any, *, detail: dict[str, Any]) -> str:
    payload = {
        "code": clean_text(item_value(raw, "standard_code") or item_value(raw, "code")),
        "name": clean_text(item_value(raw, "standard_name") or item_value(raw, "name") or item_value(raw, "chName")),
        "status": clean_text(detail.get("standard_status") or item_value(raw, "standard_status") or item_value(raw, "source_status_raw")),
        "publish_date": clean_text(detail.get("publish_date") or item_value(raw, "publish_date")),
        "effective_date": clean_text(detail.get("effective_date") or item_value(raw, "effective_date")),
        "detail_url": clean_text(detail.get("detail_url") or item_value(raw, "detail_url")),
        "download_url": clean_text(detail.get("download_url") or item_value(raw, "download_url")),
        "online_url": clean_text(detail.get("online_url") or item_value(raw, "online_url")),
        "external_id": clean_text(item_value(raw, "external_id") or item_value(raw, "pk")),
        "category": clean_text(item_value(raw, "category") or item_value(raw, "industry") or item_value(raw, "region")),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def external_id_from_detail_url(detail_url: str) -> str:
    parsed = urlparse(detail_url or "")
    query = parse_qs(parsed.query)
    for key in ("hcno", "id"):
        values = query.get(key)
        if values:
            return clean_text(values[0])
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[-2] in {"stdDetail", "online"}:
        return clean_text(path_parts[-1])
    return ""


def deterministic_uuid(value: str) -> uuid.UUID:
    return uuid.uuid5(STANDARD_LIBRARY_UUID_NAMESPACE, clean_text(value))


def deterministic_item_external_id(*, code: str, name: str, detail_url: str, legacy_standard_id: str) -> str:
    basis = detail_url or normalize_code(code) or clean_text(legacy_standard_id) or clean_text(name)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def parse_uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def progress_percent_for_status(status: str) -> int:
    if status == "completed":
        return 100
    if status == "failed":
        return 100
    if status == "running":
        return 50
    return 0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def item_value(item: Any, key: str) -> Any:
    if item is None:
        return ""
    if isinstance(item, dict):
        return item.get(key, "")
    if is_dataclass(item):
        return getattr(item, key, "")
    return getattr(item, key, "")


def item_to_dict(item: Any) -> dict[str, Any]:
    if item is None:
        return {}
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return dict(vars(item))
    return {}
