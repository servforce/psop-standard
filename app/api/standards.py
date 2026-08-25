from __future__ import annotations

import json
import os
import tempfile
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from app.db.session import SessionLocal
from app.core.standard_config import standard_settings as settings
from app.models.entities import (
    Standard,
    StandardProcessingJob,
    StandardSearchQuery,
    StandardSearchResult,
    StandardSyncItem,
    StandardSyncJob,
)
from app.services.audit import finish_call, logged_call
from app.services.standard_update import latest_standard_update_job, standard_update_job_to_dict
from app.services.standards import MARKDOWN_KINDS, standard_markdown_object_key, standard_service

router = APIRouter(prefix="/api/standards", tags=["standards"])


@router.post("/refresh-pdfs")
def refresh_pdfs():
    raise HTTPException(
        status_code=410,
        detail="旧标准库本地目录刷新已停用。标准库历史采集请使用 collect_national_pdfs.py 等外部采集脚本。",
    )


@router.post("/upload")
async def upload_standards():
    raise HTTPException(
        status_code=410,
        detail=(
            "旧标准库 PDF 手动上传入口已废弃。"
            "标准库历史采集请运行 tools/standard-collector/scripts/collect_national_pdfs.py，"
            "解析/索引请运行 tools/standard-collector/scripts/process_standard_library_jobs.py。"
        ),
    )
    workdir = Path(settings.standard_workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    uploaded = []
    with SessionLocal() as session:
        with logged_call(session, interface_type="rest", tool_or_endpoint="POST /api/standards/upload") as call_id:
            for file in files:
                filename = file.filename or "standard.pdf"
                if Path(filename).suffix.lower() != ".pdf":
                    raise HTTPException(status_code=400, detail=f"只支持 PDF 文件: {filename}")
                fd, temp_name = tempfile.mkstemp(prefix="standard_upload_", suffix=".pdf", dir=str(workdir))
                os.close(fd)
                temp_path = Path(temp_name)
                try:
                    size = 0
                    with temp_path.open("wb") as output:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            output.write(chunk)
                    if size <= 0:
                        raise HTTPException(status_code=400, detail=f"上传文件为空: {filename}")
                    uploaded.append(
                        standard_service.upload_pdf(
                            session,
                            pdf_path=temp_path,
                            filename=filename,
                            media_type=file.content_type or "application/pdf",
                        )
                    )
                finally:
                    temp_path.unlink(missing_ok=True)
            result = {"count": len(uploaded), "standards": uploaded}
            finish_call(session, call_id, result)
            return result


@router.get("")
def list_standards():
    with SessionLocal() as session:
        standards = session.scalars(select(Standard).order_by(Standard.updated_at.desc(), Standard.created_at.desc())).all()
        return [standard_to_dict(session, item) for item in standards]


@router.get("/active")
def list_active_standards(limit: int = Query(0, ge=0, le=5000), offset: int = Query(0, ge=0)):
    with SessionLocal() as session:
        standards = standard_service.current_effective_standards(session, limit=limit, offset=offset)
        return [active_standard_to_dict(item) for item in standards]


@router.get("/updates/latest")
def get_latest_standard_update_job():
    with SessionLocal() as session:
        job = latest_standard_update_job(session)
        if job is None:
            return {"status": "none"}
        return standard_update_job_to_dict(job)


@router.post("/index/rebuild")
def rebuild_standard_search_index():
    raise HTTPException(
        status_code=410,
        detail="旧标准库手动重建索引入口已废弃。请使用新标准库 processing job 或 process_standard_library_jobs.py。",
    )
    with SessionLocal() as session:
        with logged_call(session, interface_type="rest", tool_or_endpoint="POST /api/standards/index/rebuild") as call_id:
            try:
                result = standard_service.rebuild_search_index(session)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finish_call(
                session,
                call_id,
                {
                    "indexed_count": result["indexed_count"],
                    "failed_count": result["failed_count"],
                    "embedding_model": result["embedding_model"],
                    "embedding_dimensions": result["embedding_dimensions"],
                },
            )
            return result


@router.post("/{standard_id}/index/rebuild")
def rebuild_one_standard_search_index(standard_id: str):
    raise HTTPException(
        status_code=410,
        detail="旧标准库单条重建索引入口已废弃。请使用新标准库 processing job 或 process_standard_library_jobs.py。",
    )
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/{standard_id}/index/rebuild",
            request={"standard_id": standard_id},
            standard_id=standard_id,
        ) as call_id:
            try:
                result = standard_service.index_standard(session, standard_id)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finish_call(session, call_id, result)
            return result


@router.get("/search/history")
def search_history(limit: int = 0):
    with SessionLocal() as session:
        statement = (
            select(StandardSearchQuery)
            .where(StandardSearchQuery.caller_type.in_(("web", "api")))
            .order_by(StandardSearchQuery.created_at.desc())
        )
        if limit > 0:
            statement = statement.limit(max(1, min(limit, 1000)))
        rows = session.scalars(statement).all()
        return [search_query_to_dict(session, row) for row in rows]


@router.post("/openstd/crawl")
def create_openstd_crawl_job():
    raise HTTPException(
        status_code=410,
        detail=(
            "OpenSTD 前端一键爬取入口已废弃。"
            "国家标准历史采集请在项目外运行 tools/standard-collector/scripts/collect_national_pdfs.py，"
            "解析/索引请运行 tools/standard-collector/scripts/process_standard_library_jobs.py。"
        ),
    )


@router.get("/openstd/crawl/latest")
def get_latest_openstd_crawl_job():
    return {"status": "disabled", "message": "OpenSTD 前端一键爬取入口已废弃，请使用外部历史采集脚本。"}


@router.get("/openstd/crawl/{job_id}")
def get_openstd_crawl_job(job_id: str):
    raise HTTPException(status_code=410, detail="OpenSTD 前端一键爬取入口已废弃，请使用外部历史采集脚本。")


@router.get("/openstd/crawl/{job_id}/items")
def list_openstd_crawl_items(job_id: str, status: str = Query("", alias="status"), limit: int = 100):
    raise HTTPException(status_code=410, detail="OpenSTD 前端一键爬取入口已废弃，请使用外部历史采集脚本。")


@router.get("/{standard_id}")
def get_standard(standard_id: str):
    with SessionLocal() as session:
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise HTTPException(status_code=404, detail="standard not found")
        return standard_to_dict(session, standard)


@router.post("/{standard_id}/materialize")
def materialize_standard(standard_id: str):
    raise HTTPException(
        status_code=410,
        detail="旧标准库手动解析入口已废弃。请使用新标准库 materialize processing job。",
    )
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/{standard_id}/materialize",
            request={"standard_id": standard_id},
            standard_id=standard_id,
        ) as call_id:
            standard = session.get(Standard, standard_id)
            if standard is None:
                raise HTTPException(status_code=404, detail="standard not found")
            running_job = latest_processing_job(session, standard_id, statuses={"running"})
            if running_job is not None:
                result = processing_job_to_dict(running_job)
                finish_call(session, call_id, result)
                return result
            job = StandardProcessingJob(
                id=uuid.uuid4().hex,
                standard_id=standard_id,
                status="running",
                stage="starting",
                progress_percent=1,
                message="解析记录已创建，准备解析标准 PDF。",
            )
            standard.materialize_status = "processing"
            session.add(job)
            session.add(standard)
            session.commit()
            result = processing_job_to_dict(job)
            background_tasks.add_task(run_standard_materialize_job, standard_id, job.id)
            finish_call(session, call_id, result)
            return result


@router.get("/{standard_id}/materialize-status")
def get_materialize_status(standard_id: str):
    with SessionLocal() as session:
        job = latest_processing_job(session, standard_id)
        if job is not None:
            return processing_job_to_dict(job)
    return standard_service.get_materialize_progress(standard_id)


@router.get("/{standard_id}/markdown/{kind}", response_class=PlainTextResponse)
def get_standard_markdown(standard_id: str, kind: str):
    if kind not in MARKDOWN_KINDS:
        raise HTTPException(status_code=400, detail="unsupported markdown kind")
    with SessionLocal() as session:
        try:
            result = standard_service.get_markdown(session, standard_id, kind)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result["markdown"]


@router.get("/{standard_id}/markdown.zip")
def download_standard_markdown_zip(standard_id: str):
    with SessionLocal() as session:
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise HTTPException(status_code=404, detail="standard not found")
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for kind in ("overview", "structure", "logic", "body"):
                try:
                    result = standard_service.get_markdown(session, standard_id, kind)
                except Exception as exc:
                    raise HTTPException(status_code=404, detail=f"{kind}.md not found: {exc}") from exc
                archive.writestr(f"{kind}.md", result["markdown"])
        buffer.seek(0)
        safe_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in standard.name) or standard_id
        download_name = f"{standard.name or standard_id}-markdown.zip"
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{safe_name}-markdown.zip"; '
                    f"filename*=UTF-8''{quote(download_name, safe='')}"
                )
            },
        )


@router.post("/search")
def search_standards(query: str = Query(...), limit: int = Query(settings.standard_search_result_limit, ge=1, le=20)):
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/search",
            request={"query": query, "limit": limit},
        ) as call_id:
            result = standard_service.search(session, query=query, limit=limit)
            search_id = uuid.uuid4().hex
            saved_match_count = standard_service.save_standard_matches(
                session,
                matches=result.get("matches") or [],
                search_id=search_id,
                query_text=query,
                caller_type="web",
                limit=limit,
                mode=result.get("mode") or "pgvector_overview",
            )
            finish_call(
                session,
                call_id,
                {
                    "search_id": search_id,
                    "mode": result.get("mode"),
                    "embedding_model": result.get("embedding_model"),
                    "embedding_dimensions": result.get("embedding_dimensions"),
                    "match_count": len(result.get("matches") or []),
                    "saved_match_count": saved_match_count,
                    "excluded_count": len(result.get("excluded") or []),
                    "matches": [
                        {
                            "standard_id": item.get("standard_id"),
                            "standard_name": item.get("standard_name"),
                            "decision": item.get("decision"),
                            "score": item.get("score"),
                        }
                        for item in (result.get("matches") or [])[:5]
                    ],
                    "message": result.get("message"),
                },
            )
            return result


def standard_to_dict(session, standard: Standard) -> dict:
    return {
        "id": standard.id,
        "name": standard.name,
        "code": standard.code,
        "status": standard.materialize_status,
        "source_status": standard.source_status,
        "source_status_raw": standard.source_status_raw,
        "standard_type": standard.standard_type,
        "standard_category": standard.standard_category,
        "standard_org": standard.standard_org,
        "publish_date": standard.publish_date,
        "effective_date": standard.effective_date,
        "detail_url": standard.detail_url,
        "source_pdf_bucket": standard.source_pdf_bucket,
        "source_pdf_object_key": standard.source_pdf_object_key,
        "source_pdf_hash": standard.source_pdf_hash,
        "source_pdf_size_bytes": standard.source_pdf_size_bytes,
        "materialize_status": standard.materialize_status,
        "materialize_error": standard.materialize_error,
        "materialized_at": standard.materialized_at.isoformat() if standard.materialized_at else None,
        "index_status": standard.index_status,
        "indexed_at": standard.indexed_at.isoformat() if standard.indexed_at else None,
        "index_error": standard.index_error,
        "artifacts": {
            kind: getattr(standard, f"{kind}_md_object_key", "") or standard_markdown_object_key(standard.id, kind)
            for kind in sorted(MARKDOWN_KINDS)
        },
        "last_synced_at": standard.last_synced_at.isoformat() if standard.last_synced_at else None,
        "created_at": standard.created_at.isoformat() if standard.created_at else None,
        "updated_at": standard.updated_at.isoformat() if standard.updated_at else None,
    }


def active_standard_to_dict(standard: Standard) -> dict:
    return {
        "id": standard.id,
        "name": standard.name,
        "code": standard.code,
        "standard_type": standard.standard_type,
        "standard_category": standard.standard_category,
        "standard_org": standard.standard_org,
        "source_status": standard.source_status,
        "source_status_raw": standard.source_status_raw,
        "publish_date": standard.publish_date,
        "effective_date": standard.effective_date,
        "materialize_status": standard.materialize_status,
        "materialized_at": standard.materialized_at.isoformat() if standard.materialized_at else None,
        "index_status": standard.index_status,
        "indexed_at": standard.indexed_at.isoformat() if standard.indexed_at else None,
        "last_synced_at": standard.last_synced_at.isoformat() if standard.last_synced_at else None,
        "last_status_checked_at": standard.last_status_checked_at.isoformat() if standard.last_status_checked_at else None,
        "updated_at": standard.updated_at.isoformat() if standard.updated_at else None,
    }


def run_standard_materialize_job(standard_id: str, job_id: str) -> None:
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="background",
            tool_or_endpoint="standard_materialize_job",
            request={"standard_id": standard_id, "job_id": job_id},
            standard_id=standard_id,
        ) as call_id:
            result = standard_service.materialize(session, standard_id, job_id=job_id)
            finish_call(session, call_id, result)


def latest_processing_job(
    session,
    standard_id: str,
    *,
    statuses: set[str] | None = None,
) -> StandardProcessingJob | None:
    statement = select(StandardProcessingJob).where(StandardProcessingJob.standard_id == standard_id)
    if statuses:
        statement = statement.where(StandardProcessingJob.status.in_(statuses))
    statement = statement.order_by(StandardProcessingJob.created_at.desc())
    return session.scalars(statement).first()


def processing_job_to_dict(job: StandardProcessingJob) -> dict:
    return {
        "standard_id": job.standard_id,
        "job_id": job.id,
        "sync_job_id": job.sync_job_id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.stage,
        "progress_percent": job.progress_percent,
        "message": job.message,
        "error": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def search_query_to_dict(session, row: StandardSearchQuery) -> dict:
    results = session.scalars(
        select(StandardSearchResult)
        .where(StandardSearchResult.query_id == row.id)
        .order_by(StandardSearchResult.rank.asc())
    ).all()
    matches = []
    for item in results:
        evidence = []
        if item.evidence:
            try:
                parsed = json.loads(item.evidence)
                evidence = parsed if isinstance(parsed, list) else [str(parsed)]
            except json.JSONDecodeError:
                evidence = [item.evidence]
        matches.append(
            {
                "standard_id": item.standard_id,
                "index_id": item.index_id,
                "rank": item.rank,
                "score": item.score,
                "decision": item.match_level,
                "match_level": item.match_level,
                "reason": item.reason,
                "evidence": evidence,
            }
        )
    return {
        "id": row.id,
        "query": row.query_text,
        "limit": row.limit,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
        "mode": row.mode,
        "embedding_model": row.embedding_model,
        "embedding_dimensions": row.embedding_dimensions,
        "match_count": row.result_count,
        "excluded_count": 0,
        "matches": matches,
        "message": "",
    }
