from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

from app.core.config import settings
from app.models.standard_library import StandardLibraryStandard, StandardProcessingJob
from app.services.standards import MaterializedStandard
from app.services.storage import StoredObject
from app.services.standard_library_materialize import StandardLibraryMaterializeService


class FakeStorage:
    def __init__(self) -> None:
        self.downloads = []
        self.uploads = []

    def download_file(self, *, bucket: str, object_key: str, path: Path) -> None:
        self.downloads.append({"bucket": bucket, "object_key": object_key, "path": path})
        path.write_bytes(b"%PDF-1.7\n")

    def upload_bytes(self, *, object_key: str, content: bytes, media_type: str, bucket: str | None = None) -> StoredObject:
        self.uploads.append(
            {
                "bucket": bucket,
                "object_key": object_key,
                "content": content,
                "media_type": media_type,
            }
        )
        return StoredObject(
            bucket=bucket or settings.object_store_bucket,
            object_key=object_key,
            media_type=media_type,
            size_bytes=len(content),
            checksum="checksum",
        )


class FakeSession:
    def __init__(self, standard: StandardLibraryStandard, job: StandardProcessingJob) -> None:
        self.standard = standard
        self.job = job
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


def test_standard_library_materialize_writes_markdown_artifacts_and_status(monkeypatch, tmp_path):
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
        source_pdf_bucket="openstd",
        source_pdf_object_key="pdfs/demo.pdf",
        materialize_status="pending",
        index_status="indexed",
    )
    job = StandardProcessingJob(
        id=job_id,
        job_type="materialize",
        standard_id=standard_id,
        status="pending",
        stage="queued",
        progress_percent=0,
    )
    fake_session = FakeSession(standard, job)
    fake_storage = FakeStorage()
    created_index_jobs = []

    def fake_build_four_markdowns(**kwargs):
        assert kwargs["standard_name"] == "Demo standard"
        assert kwargs["source_pdf"] == "demo.pdf"
        assert kwargs["pdf_path"].exists()
        kwargs["progress"]("generating_body", 25, "body")
        return MaterializedStandard(
            standard_id=str(standard_id),
            markdown={
                "body": "---\ndocument_role: standard_body\n---\n# Body",
                "structure": "---\ndocument_role: standard_structure\n---\n# Structure",
                "logic": "---\ndocument_role: standard_logic\n---\n# Logic",
                "overview": "---\ndocument_role: standard_overview\n---\n# Overview",
            },
        )

    monkeypatch.setattr("app.services.standard_library_materialize.build_four_markdowns", fake_build_four_markdowns)
    monkeypatch.setattr(
        "app.services.standard_library_materialize.settings",
        replace(settings, standard_workdir=str(tmp_path)),
    )
    monkeypatch.setattr(
        "app.services.standard_library_index.standard_library_index_service",
        type(
            "FakeIndexService",
            (),
            {
                "create_index_job": lambda self, session, standard_id, **kwargs: created_index_jobs.append(
                    {"standard_id": standard_id, **kwargs}
                )
            },
        )(),
    )

    result = StandardLibraryMaterializeService(storage=fake_storage).materialize(
        fake_session,
        standard_id,
        job_id=job_id,
    )

    assert result["status"] == "materialized"
    assert fake_storage.downloads[0]["bucket"] == "openstd"
    assert {upload["object_key"].rsplit("/", 1)[-1] for upload in fake_storage.uploads} == {
        "standard_body.md",
        "standard_structure.md",
        "standard_logic.md",
        "standard_overview.md",
    }
    assert {upload["bucket"] for upload in fake_storage.uploads} == {settings.standard_library_object_store_bucket}
    assert standard.materialize_status == "materialized"
    assert standard.materialize_error is None
    assert standard.materialized_at is not None
    assert standard.index_status == "pending"
    assert standard.index_error is None
    assert standard.indexed_at is None
    assert standard.body_md_object_key.endswith("/standard_body.md")
    assert standard.structure_md_object_key.endswith("/standard_structure.md")
    assert standard.logic_md_object_key.endswith("/standard_logic.md")
    assert standard.overview_md_object_key.endswith("/standard_overview.md")
    assert created_index_jobs == [{"standard_id": standard_id, "source_sync_job_id": None, "source_sync_item_id": None, "priority": 100}]
    assert job.status == "completed"
    assert job.stage == "completed"
    assert int(job.progress_percent) == 100
