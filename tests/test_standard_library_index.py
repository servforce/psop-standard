from __future__ import annotations

import uuid

from app.core.config import settings
from app.models.standard_library import StandardLibraryStandard, StandardProcessingJob
from app.services.standard_library_index import StandardLibraryIndexService


class FakeStorage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.reads = []

    def get_bytes(self, *, bucket: str, object_key: str) -> bytes:
        self.reads.append({"bucket": bucket, "object_key": object_key})
        return self.content.encode("utf-8")


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.inputs = []

    def embed(self, text_value: str) -> list[float]:
        self.inputs.append(text_value)
        return [0.1] * settings.standard_embedding_dimensions


class FakeSession:
    def __init__(self, standard: StandardLibraryStandard, job: StandardProcessingJob) -> None:
        self.standard = standard
        self.job = job
        self.executed = []
        self.commits = 0

    def get(self, model, row_id):
        parsed_id = uuid.UUID(str(row_id))
        if model is StandardLibraryStandard and parsed_id == self.standard.id:
            return self.standard
        if model is StandardProcessingJob and parsed_id == self.job.id:
            return self.job
        return None

    def add(self, row) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def execute(self, statement, params=None):
        self.executed.append({"statement": str(statement), "params": params or {}})


def test_standard_library_index_reads_overview_and_writes_pgvector_index():
    standard_id = uuid.uuid4()
    job_id = uuid.uuid4()
    standard = StandardLibraryStandard(
        id=standard_id,
        code="GB/T 1-2024",
        code_normalized="GB/T1-2024",
        name="Demo standard",
        source="national",
        source_label="National standards",
        category="recommended",
        category_label="Recommended national standard",
        official_status="current",
        file_access_type="downloadable",
        source_pdf_object_key="pdfs/demo.pdf",
        overview_md_object_key="markdown/demo/standard_overview.md",
        materialize_status="materialized",
        index_status="pending",
    )
    job = StandardProcessingJob(
        id=job_id,
        job_type="index",
        standard_id=standard_id,
        status="pending",
        stage="queued",
        progress_percent=0,
    )
    overview = """---
standard_name: "Demo standard"
standard_number: "GB/T 1-2024"
source_pdf: "demo.pdf"
document_role: "standard_overview"
schema_version: "simple-1.0"
---

# 标准检索概要

## 1. 标准基本信息

Demo standard applies to safety checks.
"""
    storage = FakeStorage(overview)
    embedding_client = FakeEmbeddingClient()
    session = FakeSession(standard, job)

    result = StandardLibraryIndexService(storage=storage, embedding_client=embedding_client).index_standard(
        session,
        standard_id,
        job_id=job_id,
    )

    assert result["status"] == "indexed"
    assert result["index_kind"] == "overview"
    assert storage.reads == [
        {"bucket": settings.standard_library_object_store_bucket, "object_key": "markdown/demo/standard_overview.md"}
    ]
    assert "标准名称：Demo standard" in embedding_client.inputs[0]
    assert "document_role" not in embedding_client.inputs[0]
    assert len(session.executed) == 2
    assert "DELETE FROM standard_indexes" in session.executed[0]["statement"]
    assert "INSERT INTO standard_indexes" in session.executed[1]["statement"]
    insert_params = session.executed[1]["params"]
    assert insert_params["standard_id"] == str(standard_id)
    assert insert_params["index_kind"] == "overview"
    assert insert_params["embedding_model"] == settings.standard_embedding_model
    assert insert_params["embedding_dimensions"] == settings.standard_embedding_dimensions
    assert insert_params["schema_version"] == "overview-1.0"
    assert standard.index_status == "indexed"
    assert standard.index_error is None
    assert standard.indexed_at is not None
    assert job.status == "completed"
    assert job.stage == "completed"
    assert int(job.progress_percent) == 100
