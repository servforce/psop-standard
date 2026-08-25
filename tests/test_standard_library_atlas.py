from __future__ import annotations

import math

from app.services.standard_library_atlas import build_input_hash, parse_vector, project_embeddings


def test_parse_pgvector_text():
    assert parse_vector("[0.1, -0.2, 3]") == [0.1, -0.2, 3.0]
    assert parse_vector("") == []
    assert parse_vector([1, "2.5"]) == [1.0, 2.5]


def test_project_embeddings_handles_small_samples():
    assert project_embeddings([]) == []
    assert project_embeddings([[0.1, 0.2]]) == [(0.0, 0.0)]
    assert project_embeddings([[0.1, 0.2], [0.3, 0.4]]) == [(-1.0, 0.0), (1.0, 0.0)]


def test_project_embeddings_returns_bounded_non_nan_coordinates():
    points = project_embeddings(
        [
            [0.9, 0.1, 0.0],
            [0.8, 0.2, 0.1],
            [0.1, 0.9, 0.1],
            [0.2, 0.8, 0.2],
        ]
    )

    assert len(points) == 4
    for x, y in points:
        assert not math.isnan(x)
        assert not math.isnan(y)
        assert -1.0 <= x <= 1.0
        assert -1.0 <= y <= 1.0


def test_build_input_hash_is_stable_and_order_sensitive():
    rows = [
        {
            "standard_id": "standard-1",
            "index_id": "index-1",
            "content_hash": "content-1",
            "embedding": "[0.1,0.2]",
        },
        {
            "standard_id": "standard-2",
            "index_id": "index-2",
            "content_hash": "content-2",
            "embedding": "[0.3,0.4]",
        },
    ]

    assert build_input_hash(rows) == build_input_hash(list(rows))
    assert build_input_hash(rows) != build_input_hash(list(reversed(rows)))
