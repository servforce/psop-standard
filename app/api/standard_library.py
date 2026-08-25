from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.db.standard_library import StandardLibrarySessionLocal
from app.services.standard_library import MARKDOWN_KINDS, SearchFailedError, standard_library_service


router = APIRouter(prefix="/api/standard-library", tags=["standard-library"])


@router.get("/summary")
def get_standard_library_summary():
    with StandardLibrarySessionLocal() as session:
        return standard_library_service.summary(session)


@router.get("/catalog")
def list_standard_library_catalog(
    query: str = Query("", description="Standard code exact match or standard name keyword."),
    code: str = Query("", description="Exact standard code."),
    keyword: str = Query("", description="Standard name keyword."),
    source: str = Query("", description="national, industry, local, or empty for all."),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
):
    with StandardLibrarySessionLocal() as session:
        try:
            return standard_library_service.catalog(
                session,
                query=query,
                code=code,
                keyword=keyword,
                source=source,
                page=page,
                page_size=page_size,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/atlas")
def get_standard_library_atlas(color_by: str = Query("source")):
    with StandardLibrarySessionLocal() as session:
        try:
            return standard_library_service.atlas(session, color_by=color_by)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/search")
def search_standard_library(
    query: str = Query(..., min_length=0, description="Natural language query."),
    limit: int = Query(20, ge=1, le=50),
    caller: str = Query("frontend"),
):
    with StandardLibrarySessionLocal() as session:
        try:
            return standard_library_service.search(
                session,
                query=query,
                limit=limit,
                caller=caller,
            )
        except SearchFailedError as exc:
            raise HTTPException(
                status_code=502,
                detail={"search_id": exc.search_id, "message": str(exc)},
            ) from exc


@router.get("/search/history")
def list_standard_library_search_history(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    with StandardLibrarySessionLocal() as session:
        return standard_library_service.search_history(session, limit=limit, offset=offset)


@router.get("/search/history/{search_id}")
def get_standard_library_search_history_detail(search_id: str):
    with StandardLibrarySessionLocal() as session:
        try:
            return standard_library_service.search_history_detail(session, search_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{standard_id}")
def get_standard_library_detail(standard_id: str):
    with StandardLibrarySessionLocal() as session:
        try:
            return standard_library_service.detail(session, standard_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{standard_id}/markdown/{kind}", response_class=PlainTextResponse)
def get_standard_library_markdown(standard_id: str, kind: str):
    if kind not in MARKDOWN_KINDS:
        raise HTTPException(status_code=400, detail="unsupported markdown kind")
    with StandardLibrarySessionLocal() as session:
        try:
            return standard_library_service.markdown(session, standard_id, kind)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=502, detail="markdown is not valid utf-8") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"failed to read markdown: {exc}") from exc
