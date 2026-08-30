"""Integration tests for conditional file metadata replacement."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from star.core.errors import (
    FILE_NOT_FOUND,
    INVALID_REQUEST,
    PRECONDITION_FAILED,
    PRECONDITION_REQUIRED,
    UNPROCESSABLE_ENTITY,
)

# ============================================================================
# Helpers
# ============================================================================


def _upload_text_file_and_get_id(
    client: TestClient,
    auth_headers: dict[str, str],
) -> UUID:
    """Upload the fixture text file and return its managed UUID."""

    response = client.post(
        "/v1/files",
        headers=auth_headers,
        files={"file": ("report.txt", b"STAR report\n", "text/plain")},
    )

    assert response.status_code == 201
    return UUID(response.json()["data"]["file"]["id"])


def _metadata_etag(
    client: TestClient,
    auth_headers: dict[str, str],
    file_id: UUID,
) -> str:
    """Retrieve one file's current ETag through its public representation."""

    response = client.get(f"/v1/files/{file_id}", headers=auth_headers)

    assert response.status_code == 200
    return response.headers["etag"]


# ============================================================================
# Conditional Replacement
# ============================================================================


def test_files_put_replaces_editable_metadata_and_returns_new_etag(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a ready uploaded file and its current ETag
    WHEN PUT replaces the display filename and complete tag set
    THEN STAR persists canonical metadata and returns a fresh ETag
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)
        current_etag = _metadata_etag(client, auth_headers, file_id)

        response = client.put(
            f"/v1/files/{file_id}",
            headers={**auth_headers, "If-Match": current_etag},
            json={"file_name": "quarterly-report.txt", "tags": ["Q3", "finance"]},
        )
        retrieved = client.get(f"/v1/files/{file_id}", headers=auth_headers)
        download = client.get(f"/v1/files/{file_id}/content", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["etag"] != current_etag
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"]["file"]["file_name"] == "quarterly-report.txt"
    assert body["data"]["file"]["tags"] == ["finance", "q3"]
    assert body["data"]["file"]["original_filename"] == "report.txt"
    assert retrieved.json()["data"]["file"] == body["data"]["file"]
    assert retrieved.headers["etag"] == response.headers["etag"]
    assert "quarterly-report.txt" in download.headers["content-disposition"]


def test_files_put_rejects_stale_etag_without_overwriting_metadata(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN two clients holding the same file ETag
    WHEN one update succeeds before the other submits its replacement
    THEN the stale request receives PRECONDITION_FAILED without an overwrite
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)
        stale_etag = _metadata_etag(client, auth_headers, file_id)
        first = client.put(
            f"/v1/files/{file_id}",
            headers={**auth_headers, "If-Match": stale_etag},
            json={"file_name": "first.txt", "tags": ["first"]},
        )
        second = client.put(
            f"/v1/files/{file_id}",
            headers={**auth_headers, "If-Match": stale_etag},
            json={"file_name": "second.txt", "tags": ["second"]},
        )
        retrieved = client.get(f"/v1/files/{file_id}", headers=auth_headers)

    assert first.status_code == 200
    assert second.status_code == PRECONDITION_FAILED.http_status
    assert second.json()["error"]["code"] == PRECONDITION_FAILED.code
    assert retrieved.json()["data"]["file"]["file_name"] == "first.txt"
    assert retrieved.json()["data"]["file"]["tags"] == ["first"]


# ============================================================================
# Boundary Rejections
# ============================================================================


def test_files_put_requires_a_valid_current_if_match_header(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN an uploaded file
    WHEN PUT omits or malforms the required If-Match precondition
    THEN STAR returns the corresponding stable precondition or request error
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)
        without_header = client.put(
            f"/v1/files/{file_id}",
            headers=auth_headers,
            json={"file_name": "report.txt", "tags": []},
        )
        malformed = client.put(
            f"/v1/files/{file_id}",
            headers={**auth_headers, "If-Match": 'W/"not-a-star-etag"'},
            json={"file_name": "report.txt", "tags": []},
        )

    assert without_header.status_code == PRECONDITION_REQUIRED.http_status
    assert without_header.json()["error"]["code"] == PRECONDITION_REQUIRED.code
    assert malformed.status_code == INVALID_REQUEST.http_status
    assert malformed.json()["error"]["code"] == INVALID_REQUEST.code


def test_files_put_rejects_unknown_fields_and_policy_invalid_metadata(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN an uploaded file and a current ETag
    WHEN PUT includes mass-assignment fields or invalid mutable values
    THEN the request is rejected in the safe 422 validation envelope
    """

    app = create_upload_app()

    with TestClient(app) as client:
        file_id = _upload_text_file_and_get_id(client, auth_headers)
        current_etag = _metadata_etag(client, auth_headers, file_id)
        unknown_field = client.put(
            f"/v1/files/{file_id}",
            headers={**auth_headers, "If-Match": current_etag},
            json={
                "file_name": "report.txt",
                "tags": [],
                "status": "ready",
            },
        )
        invalid_values = client.put(
            f"/v1/files/{file_id}",
            headers={**auth_headers, "If-Match": current_etag},
            json={"file_name": "report.txt", "tags": ["not allowed"]},
        )

    for response in (unknown_field, invalid_values):
        assert response.status_code == UNPROCESSABLE_ENTITY.http_status
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == UNPROCESSABLE_ENTITY.code
        errors = body["error"].get("details", {}).get("errors", [])
        assert errors
        assert all(set(error).issubset({"type", "loc", "msg"}) for error in errors)


def test_files_put_rejects_changed_extension_and_missing_blob(
    create_upload_app,
    auth_headers,
    tmp_path: Path,
):
    """
    GIVEN uploaded files with a current ETag
    WHEN PUT changes the extension or the ready blob is absent
    THEN STAR rejects the mutation without publishing unsafe metadata
    """

    app = create_upload_app()

    with TestClient(app) as client:
        first_id = _upload_text_file_and_get_id(client, auth_headers)
        first_etag = _metadata_etag(client, auth_headers, first_id)
        changed_extension = client.put(
            f"/v1/files/{first_id}",
            headers={**auth_headers, "If-Match": first_etag},
            json={"file_name": "report.pdf", "tags": []},
        )

        second_id = _upload_text_file_and_get_id(client, auth_headers)
        (tmp_path / "data" / "files" / "blobs" / f"file_{second_id}.bin").unlink()
        second_etag = _metadata_etag(client, auth_headers, second_id)
        missing_blob = client.put(
            f"/v1/files/{second_id}",
            headers={**auth_headers, "If-Match": second_etag},
            json={"file_name": "report.txt", "tags": []},
        )

    assert changed_extension.status_code == INVALID_REQUEST.http_status
    assert changed_extension.json()["error"]["code"] == INVALID_REQUEST.code
    assert missing_blob.status_code == FILE_NOT_FOUND.http_status
    assert missing_blob.json()["error"]["code"] == FILE_NOT_FOUND.code


def test_files_put_rejects_nonexistent_file_after_validating_precondition_shape(
    create_upload_app,
    auth_headers,
):
    """
    GIVEN a valid strong ETag with no corresponding metadata record
    WHEN PUT targets a random file UUID
    THEN STAR returns FILE_NOT_FOUND without disclosing storage details
    """

    app = create_upload_app()

    with TestClient(app) as client:
        response = client.put(
            f"/v1/files/{uuid4()}",
            headers={**auth_headers, "If-Match": '"' + "0" * 64 + '"'},
            json={"file_name": "report.txt", "tags": []},
        )

    assert response.status_code == FILE_NOT_FOUND.http_status
    assert response.json()["error"]["code"] == FILE_NOT_FOUND.code
