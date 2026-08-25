from __future__ import annotations

import math
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.standard_config import standard_settings as settings
from app.models.standard_library import (
    StandardAtlasPoint,
    StandardAtlasProjection,
    StandardIndex,
    StandardLibraryStandard,
    StandardSearchQuery,
    StandardSearchResult,
    StandardSyncJob,
)
from app.services.standards import StandardEmbeddingClient, vector_literal
from app.services.storage import storage_service


MARKDOWN_KINDS = {"overview", "structure", "logic", "body"}
SOURCE_LABELS = {
    "national": "国家标准",
    "industry": "行业标准",
    "local": "地方标准",
}
ATLAS_COLORS = [
    "#2563eb",
    "#16a34a",
    "#dc2626",
    "#9333ea",
    "#0891b2",
    "#ca8a04",
    "#db2777",
    "#4f46e5",
]


class StandardLibraryService:
    def summary(self, session: Session) -> dict[str, Any]:
        effective_count = session.scalar(select(func.count()).select_from(self._effective_statement().subquery())) or 0
        latest_job = session.scalars(
            select(StandardSyncJob)
            .where(StandardSyncJob.job_type == "scheduled_update")
            .order_by(StandardSyncJob.created_at.desc())
            .limit(1)
        ).first()
        if latest_job is None:
            return {
                "effective_standard_count": effective_count,
                "latest_update_at": None,
                "cycle_status": "not_started",
                "new_active_count": 0,
                "expired_count": 0,
                "failed_count": 0,
                "error_message": None,
            }
        latest_update_at = (
            latest_job.finished_at
            or latest_job.heartbeat_at
            or latest_job.started_at
            or latest_job.updated_at
            or latest_job.created_at
        )
        return {
            "effective_standard_count": effective_count,
            "latest_update_job_id": str(latest_job.id),
            "latest_update_at": format_dt(latest_update_at),
            "cycle_status": self._cycle_status(latest_job),
            "new_active_count": int(latest_job.new_active_count or 0),
            "expired_count": int(latest_job.expired_count or 0),
            "failed_count": int(latest_job.failed_count or 0),
            "error_message": latest_job.error_message,
        }

    def catalog(
        self,
        session: Session,
        *,
        query: str = "",
        code: str = "",
        keyword: str = "",
        source: str = "",
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(page_size, 50))
        source = source.strip()
        if source and source not in SOURCE_LABELS:
            raise ValueError("unsupported source")

        statement = self._effective_statement()
        if source:
            statement = statement.where(StandardLibraryStandard.source == source)

        query = query.strip()
        code = code.strip()
        keyword = keyword.strip()
        has_search = bool(query or code or keyword)
        if code:
            statement = statement.where(StandardLibraryStandard.code_normalized == normalize_code(code))
        if keyword:
            statement = statement.where(StandardLibraryStandard.name.ilike(f"%{keyword}%"))
        if query:
            normalized_query = normalize_code(query)
            statement = statement.where(
                or_(
                    StandardLibraryStandard.code_normalized == normalized_query,
                    StandardLibraryStandard.name.ilike(f"%{query}%"),
                )
            )
        if not has_search:
            statement = statement.where(StandardLibraryStandard.publish_date >= date.today() - timedelta(days=31))

        total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = session.scalars(
            statement.order_by(
                StandardLibraryStandard.publish_date.desc().nullslast(),
                StandardLibraryStandard.updated_at.desc(),
                StandardLibraryStandard.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        total_pages = max(1, math.ceil(total / page_size)) if total else 0
        return {
            "items": [self.standard_list_item(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_search": has_search,
        }

    def detail(self, session: Session, standard_id: str) -> dict[str, Any]:
        standard = self.get_materialized_standard(session, standard_id)
        return {
            **self.standard_detail_item(standard),
            "markdown": {
                kind: markdown_state(standard, kind)
                for kind in ("overview", "structure", "logic", "body")
            },
        }

    def markdown(self, session: Session, standard_id: str, kind: str) -> str:
        if kind not in MARKDOWN_KINDS:
            raise ValueError("unsupported markdown kind")
        standard = self.get_materialized_standard(session, standard_id)
        object_key = getattr(standard, f"{kind}_md_object_key") or ""
        if not object_key:
            raise LookupError(f"{kind} markdown is not available")
        content = storage_service.get_bytes(
            bucket=settings.standard_library_object_store_bucket,
            object_key=object_key,
        )
        return content.decode("utf-8")

    def atlas(self, session: Session, *, color_by: str = "source") -> dict[str, Any]:
        color_by = color_by or "source"
        if color_by not in {"source", "category"}:
            raise ValueError("unsupported color_by")
        effective_count = session.scalar(select(func.count()).select_from(self._effective_statement().subquery())) or 0
        projection = session.scalars(
            select(StandardAtlasProjection)
            .where(
                StandardAtlasProjection.status == "completed",
                StandardAtlasProjection.is_current.is_(True),
            )
            .order_by(StandardAtlasProjection.completed_at.desc().nullslast(), StandardAtlasProjection.created_at.desc())
            .limit(1)
        ).first()
        if projection is None:
            latest_projection = session.scalars(
                select(StandardAtlasProjection)
                .order_by(StandardAtlasProjection.created_at.desc())
                .limit(1)
            ).first()
            status = "not_ready"
            if latest_projection and latest_projection.status in {"pending", "running"}:
                status = "generating"
            return empty_atlas(
                status=status,
                effective_count=effective_count,
                projection=latest_projection,
            )

        rows = session.execute(
            select(StandardAtlasPoint, StandardLibraryStandard)
            .join(StandardLibraryStandard, StandardLibraryStandard.id == StandardAtlasPoint.standard_id)
            .where(StandardAtlasPoint.projection_id == projection.id)
            .where(*self._effective_filter())
            .order_by(
                StandardLibraryStandard.source.asc(),
                StandardLibraryStandard.category.asc(),
                StandardLibraryStandard.code.asc(),
                StandardLibraryStandard.id.asc(),
            )
        ).all()

        category_indexes: dict[str, int] = {}
        categories: list[dict[str, Any]] = []
        payload = {
            "x": [],
            "y": [],
            "category": [],
            "ids": [],
            "names": [],
            "codes": [],
        }
        for point, standard in rows:
            color_key = self._atlas_color_key(standard, color_by=color_by)
            if color_key not in category_indexes:
                category_indexes[color_key] = len(categories)
                categories.append(
                    {
                        "index": category_indexes[color_key],
                        "key": color_key,
                        "name": color_key,
                        "color": ATLAS_COLORS[category_indexes[color_key] % len(ATLAS_COLORS)],
                        "count": 0,
                    }
                )
            category_index = category_indexes[color_key]
            categories[category_index]["count"] += 1
            payload["x"].append(float(point.x))
            payload["y"].append(float(point.y))
            payload["category"].append(category_index)
            payload["ids"].append(str(standard.id))
            payload["names"].append(standard.name)
            payload["codes"].append(standard.code)

        projected_count = len(rows)
        return {
            "status": "ready",
            "projection_id": str(projection.id),
            "version": projection.version,
            "algorithm": projection.algorithm,
            "distance_metric": projection.distance_metric,
            "color_by": color_by,
            "updated_at": format_dt(projection.completed_at or projection.updated_at),
            "effective_standard_count": effective_count,
            "projected_count": projected_count,
            "missing_count": max(0, effective_count - projected_count),
            "data": payload,
            "categories": categories,
        }

    def search(self, session: Session, *, query: str, limit: int = 20, caller: str = "frontend") -> dict[str, Any]:
        clean_query = query.strip()
        final_limit = max(1, min(limit, 50))
        started_at = time.perf_counter()
        if not clean_query:
            return {
                "search_id": None,
                "query": query,
                "status": "empty_query",
                "matches": [],
                "result_count": 0,
                "message": "query is empty",
            }

        indexed_count = self._searchable_index_count(session)
        if indexed_count <= 0:
            search_row = self._save_search_query(
                session,
                query_text=clean_query,
                caller=caller,
                limit=final_limit,
                result_count=0,
                latency_ms=elapsed_ms(started_at),
                status="success",
            )
            session.commit()
            return {
                "search_id": str(search_row.id),
                "query": clean_query,
                "status": "success",
                "mode": "semantic",
                "embedding_model": settings.standard_embedding_model,
                "embedding_dimensions": settings.standard_embedding_dimensions,
                "indexed_count": 0,
                "matches": [],
                "result_count": 0,
                "message": "no searchable standards",
            }

        search_row: StandardSearchQuery | None = None
        try:
            embedding = StandardEmbeddingClient.from_settings().embed(clean_query)
            query_embedding = vector_literal(embedding)
            rows = session.execute(
                text(
                    """
                    SELECT
                        i.id AS index_id,
                        s.id AS standard_id,
                        s.code AS code,
                        s.name AS name,
                        s.source AS source,
                        s.source_label AS source_label,
                        s.category AS category,
                        s.category_label AS category_label,
                        s.publish_date AS publish_date,
                        s.effective_date AS effective_date,
                        s.detail_url AS detail_url,
                        i.content AS content,
                        1 - (i.embedding <=> CAST(:query_embedding AS vector)) AS score
                    FROM standard_indexes i
                    JOIN standards s ON s.id = i.standard_id
                    WHERE (
                        (
                            s.source in ('national', 'industry')
                            AND s.official_status = 'current'
                        )
                        OR (
                            s.source = 'local'
                            AND s.official_status in ('current', 'updated_available')
                        )
                    )
                      AND s.materialize_status = 'materialized'
                      AND s.index_status = 'indexed'
                      AND i.index_kind = 'overview'
                      AND i.embedding_model = :embedding_model
                      AND i.embedding_dimensions = :embedding_dimensions
                    ORDER BY i.embedding <=> CAST(:query_embedding AS vector)
                    LIMIT :limit
                    """
                ),
                {
                    "query_embedding": query_embedding,
                    "embedding_model": settings.standard_embedding_model,
                    "embedding_dimensions": settings.standard_embedding_dimensions,
                    "limit": final_limit,
                },
            ).mappings().all()
            matches = [self._search_row_to_match(row, rank=index + 1) for index, row in enumerate(rows)]
            search_row = self._save_search_query(
                session,
                query_text=clean_query,
                caller=caller,
                limit=final_limit,
                result_count=len(matches),
                latency_ms=elapsed_ms(started_at),
                status="success",
            )
            self._save_search_results(session, search_row=search_row, matches=matches)
            session.commit()
            return {
                "search_id": str(search_row.id),
                "query": clean_query,
                "status": "success",
                "mode": "semantic",
                "embedding_model": settings.standard_embedding_model,
                "embedding_dimensions": settings.standard_embedding_dimensions,
                "indexed_count": indexed_count,
                "matches": matches,
                "result_count": len(matches),
                "searched_at": format_dt(search_row.searched_at),
                "latency_ms": search_row.latency_ms,
            }
        except Exception as exc:
            session.rollback()
            search_row = self._save_search_query(
                session,
                query_text=clean_query,
                caller=caller,
                limit=final_limit,
                result_count=0,
                latency_ms=elapsed_ms(started_at),
                status="failed",
                error_message=str(exc),
            )
            session.commit()
            raise SearchFailedError(str(exc), search_id=str(search_row.id)) from exc

    def search_history(self, session: Session, *, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        final_limit = max(1, min(limit, 100))
        offset = max(0, offset)
        total = session.scalar(select(func.count()).select_from(StandardSearchQuery)) or 0
        rows = session.scalars(
            select(StandardSearchQuery)
            .order_by(StandardSearchQuery.sort_at.desc(), StandardSearchQuery.id.desc())
            .offset(offset)
            .limit(final_limit)
        ).all()
        return {
            "items": [self.search_query_item(row) for row in rows],
            "total": total,
            "limit": final_limit,
            "offset": offset,
        }

    def search_history_detail(self, session: Session, search_id: str) -> dict[str, Any]:
        try:
            parsed_id = uuid.UUID(search_id)
        except ValueError as exc:
            raise LookupError("search history not found") from exc
        row = session.get(StandardSearchQuery, parsed_id)
        if row is None:
            raise LookupError("search history not found")
        results = session.scalars(
            select(StandardSearchResult)
            .where(StandardSearchResult.query_id == row.id)
            .order_by(StandardSearchResult.rank.asc())
        ).all()
        row.last_reused_at = datetime.now().astimezone()
        row.sort_at = row.last_reused_at
        session.add(row)
        session.commit()
        return {
            **self.search_query_item(row),
            "matches": [self.search_result_snapshot(item) for item in results],
        }

    def get_effective_standard(self, session: Session, standard_id: str) -> StandardLibraryStandard:
        try:
            parsed_id = uuid.UUID(standard_id)
        except ValueError as exc:
            raise LookupError("standard not found") from exc
        standard = session.scalars(
            self._effective_statement().where(StandardLibraryStandard.id == parsed_id).limit(1)
        ).first()
        if standard is None:
            raise LookupError("standard not found")
        return standard

    def get_materialized_standard(self, session: Session, standard_id: str) -> StandardLibraryStandard:
        try:
            parsed_id = uuid.UUID(standard_id)
        except ValueError as exc:
            raise LookupError("standard not found") from exc
        standard = session.scalars(
            select(StandardLibraryStandard)
            .where(
                StandardLibraryStandard.id == parsed_id,
                *self._officially_available_filter(),
                StandardLibraryStandard.materialize_status == "materialized",
            )
            .limit(1)
        ).first()
        if standard is None:
            raise LookupError("standard not found")
        return standard

    def standard_list_item(self, standard: StandardLibraryStandard) -> dict[str, Any]:
        return {
            "standard_id": str(standard.id),
            "code": standard.code,
            "name": standard.name,
            "source": standard.source,
            "source_label": standard.source_label or SOURCE_LABELS.get(standard.source, standard.source),
            "category": standard.category,
            "category_label": standard.category_label,
            "publish_date": format_date(standard.publish_date),
            "effective_date": format_date(standard.effective_date),
            "updated_at": format_dt(standard.updated_at),
        }

    def standard_detail_item(self, standard: StandardLibraryStandard) -> dict[str, Any]:
        return {
            "standard_id": str(standard.id),
            "code": standard.code,
            "code_normalized": standard.code_normalized,
            "name": standard.name,
            "source": standard.source,
            "source_label": standard.source_label or SOURCE_LABELS.get(standard.source, standard.source),
            "category": standard.category,
            "category_label": standard.category_label,
            "standard_org": standard.standard_org,
            "official_status": standard.official_status,
            "official_status_raw": standard.official_status_raw,
            "publish_date": format_date(standard.publish_date),
            "effective_date": format_date(standard.effective_date),
            "abolish_date": format_date(standard.abolish_date),
            "source_site": standard.source_site,
            "external_id": standard.external_id,
            "detail_url": standard.detail_url,
            "pdf_url": standard.pdf_url,
            "online_url": standard.online_url,
            "file_access_type": standard.file_access_type,
            "materialize_status": standard.materialize_status,
            "materialized_at": format_dt(standard.materialized_at),
            "index_status": standard.index_status,
            "indexed_at": format_dt(standard.indexed_at),
            "created_at": format_dt(standard.created_at),
            "updated_at": format_dt(standard.updated_at),
        }

    def search_query_item(self, row: StandardSearchQuery) -> dict[str, Any]:
        return {
            "search_id": str(row.id),
            "query": row.query_text,
            "searched_at": format_dt(row.searched_at),
            "last_reused_at": format_dt(row.last_reused_at),
            "sort_at": format_dt(row.sort_at),
            "caller": row.caller,
            "limit": row.limit,
            "search_mode": row.search_mode,
            "embedding_model": row.embedding_model,
            "embedding_dimensions": row.embedding_dimensions,
            "result_count": row.result_count,
            "latency_ms": row.latency_ms,
            "status": row.status,
            "error_message": row.error_message,
        }

    def search_result_snapshot(self, row: StandardSearchResult) -> dict[str, Any]:
        return {
            "standard_id": str(row.standard_id),
            "index_id": str(row.index_id) if row.index_id else None,
            "rank": row.rank,
            "score": float(row.score),
            "match_level": row.match_level,
            "reason": row.reason,
            "evidence": row.evidence,
            "code": row.snapshot_code,
            "name": row.snapshot_name,
            "source": row.snapshot_source,
            "source_label": SOURCE_LABELS.get(row.snapshot_source, row.snapshot_source),
            "category": row.snapshot_category,
            "publish_date": format_date(row.snapshot_publish_date),
            "effective_date": format_date(row.snapshot_effective_date),
            "detail_url": row.snapshot_detail_url,
            "snapshot_payload": row.snapshot_payload,
        }

    def _effective_statement(self) -> Select[tuple[StandardLibraryStandard]]:
        return select(StandardLibraryStandard).where(*self._effective_filter())

    def _effective_filter(self):
        return (
            *self._officially_available_filter(),
            StandardLibraryStandard.materialize_status == "materialized",
            StandardLibraryStandard.index_status == "indexed",
        )

    def _officially_available_filter(self):
        return (
            or_(
                StandardLibraryStandard.source.in_(("national", "industry"))
                & (StandardLibraryStandard.official_status == "current"),
                (StandardLibraryStandard.source == "local")
                & StandardLibraryStandard.official_status.in_(("current", "updated_available")),
            ),
        )

    def _cycle_status(self, job: StandardSyncJob) -> str:
        if job.status in {"pending", "running"}:
            return "running"
        if job.status == "completed" and int(job.failed_count or 0) > 0:
            return "completed_with_failures"
        if job.status == "completed":
            return "completed"
        if job.status == "failed":
            return "failed"
        return job.status

    def _atlas_color_key(self, standard: StandardLibraryStandard, *, color_by: str) -> str:
        if color_by == "category":
            return standard.category_label or standard.category or "未分类"
        return standard.source_label or SOURCE_LABELS.get(standard.source, standard.source)

    def _searchable_index_count(self, session: Session) -> int:
        return session.scalar(
            select(func.count())
            .select_from(StandardIndex)
            .join(StandardLibraryStandard, StandardLibraryStandard.id == StandardIndex.standard_id)
            .where(*self._effective_filter())
            .where(
                StandardIndex.index_kind == "overview",
                StandardIndex.embedding_model == settings.standard_embedding_model,
                StandardIndex.embedding_dimensions == settings.standard_embedding_dimensions,
            )
        ) or 0

    def _search_row_to_match(self, row, *, rank: int) -> dict[str, Any]:
        score = max(0.0, min(1.0, float(row["score"] or 0.0)))
        return {
            "standard_id": str(row["standard_id"]),
            "index_id": str(row["index_id"]),
            "rank": rank,
            "score": score,
            "match_level": "strong" if score >= 0.78 else "weak",
            "reason": "Matched by pgvector cosine similarity over standard_overview.md.",
            "evidence": str(row["content"] or "")[:500],
            "code": row["code"] or "",
            "name": row["name"] or "",
            "source": row["source"] or "",
            "source_label": row["source_label"] or SOURCE_LABELS.get(row["source"], row["source"]),
            "category": row["category_label"] or row["category"] or "",
            "publish_date": format_date(row["publish_date"]),
            "effective_date": format_date(row["effective_date"]),
            "detail_url": row["detail_url"],
        }

    def _save_search_query(
        self,
        session: Session,
        *,
        query_text: str,
        caller: str,
        limit: int,
        result_count: int,
        latency_ms: int,
        status: str,
        error_message: str = "",
    ) -> StandardSearchQuery:
        row = StandardSearchQuery(
            id=uuid.uuid4(),
            query_text=query_text,
            caller=caller or "frontend",
            limit=limit,
            search_mode="semantic",
            embedding_model=settings.standard_embedding_model,
            embedding_dimensions=settings.standard_embedding_dimensions,
            result_count=result_count,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message or None,
        )
        session.add(row)
        session.flush()
        return row

    def _save_search_results(
        self,
        session: Session,
        *,
        search_row: StandardSearchQuery,
        matches: list[dict[str, Any]],
    ) -> None:
        for item in matches:
            session.add(
                StandardSearchResult(
                    id=uuid.uuid4(),
                    query_id=search_row.id,
                    standard_id=uuid.UUID(item["standard_id"]),
                    index_id=uuid.UUID(item["index_id"]) if item.get("index_id") else None,
                    rank=int(item["rank"]),
                    score=float(item["score"]),
                    match_level=item.get("match_level"),
                    reason=item.get("reason"),
                    evidence=item.get("evidence"),
                    snapshot_code=item.get("code") or "",
                    snapshot_name=item.get("name") or "",
                    snapshot_source=item.get("source") or "",
                    snapshot_category=item.get("category") or None,
                    snapshot_publish_date=parse_iso_date(item.get("publish_date")),
                    snapshot_effective_date=parse_iso_date(item.get("effective_date")),
                    snapshot_detail_url=item.get("detail_url"),
                    snapshot_payload={
                        "source_label": item.get("source_label"),
                        "score": item.get("score"),
                    },
                )
            )


def empty_atlas(
    *,
    status: str,
    effective_count: int,
    projection: StandardAtlasProjection | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "projection_id": str(projection.id) if projection else None,
        "version": projection.version if projection else None,
        "algorithm": projection.algorithm if projection else None,
        "distance_metric": projection.distance_metric if projection else None,
        "updated_at": format_dt((projection.completed_at or projection.updated_at) if projection else None),
        "effective_standard_count": effective_count,
        "projected_count": 0,
        "missing_count": effective_count,
        "data": {"x": [], "y": [], "category": [], "ids": [], "names": [], "codes": []},
        "categories": [],
    }


def markdown_state(standard: StandardLibraryStandard, kind: str) -> dict[str, Any]:
    object_key = getattr(standard, f"{kind}_md_object_key") or ""
    return {
        "kind": kind,
        "available": bool(object_key),
        "object_key": object_key or None,
    }


def normalize_code(value: str) -> str:
    return "".join(value.upper().split())


def format_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def format_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


class SearchFailedError(RuntimeError):
    def __init__(self, message: str, *, search_id: str) -> None:
        super().__init__(message)
        self.search_id = search_id


standard_library_service = StandardLibraryService()
