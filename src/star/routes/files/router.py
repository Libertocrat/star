"""HTTP routes for STAR-managed file resources.

This module exposes the first RESTful file endpoint for ingesting uploads and
returning typed STAR response envelopes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from star.core.errors import StarError
from star.core.schemas.envelope import ResponseEnvelope
from star.core.utils.file_storage import iter_file_chunks
from star.routes.dependencies import get_runtime_settings
from star.routes.files.handlers.delete_file import delete_file_handler
from star.routes.files.handlers.get_file_content import get_file_content_handler
from star.routes.files.handlers.get_file_metadata import get_file_metadata_handler
from star.routes.files.handlers.list_files import list_files_handler
from star.routes.files.handlers.upload_file import (
    parse_post_file_request,
    upload_file_handler,
)
from star.routes.files.schemas import (
    DeleteFileData,
    FileListData,
    UploadFileData,
    UploadFileRequest,
    VerifyChecksumParams,
)

router = APIRouter(prefix="/v1", tags=["Files"])

post_description = (
    "**Upload and securely persist a file using STAR-managed storage.**\n\n"
    "This endpoint accepts multipart/form-data uploads and enforces a strict "
    "validation pipeline before persisting any data.\n\n"
    "Processing steps:\n"
    "- Stream upload to temporary storage\n"
    "- Compute SHA256 checksum\n"
    "- Detect MIME type using server-side inspection\n"
    "- Validate extension ↔ MIME mapping\n"
    "- Reject executable or unsafe file types\n"
    "- Enforce maximum file size limits\n"
    "- Persist blob and metadata atomically\n\n"
    "Checksum verification:\n"
    "- An optional `checksum` field may be provided\n"
    "- The value MUST be the SHA256 hash (hex-encoded) of the file contents\n"
    "- If provided, STAR verifies integrity before accepting the upload\n"
    "- Mismatches result in request rejection\n\n"
    "Storage model:\n"
    "- Files are stored as immutable blobs\n"
    "- Metadata is persisted as a JSON sidecar document\n"
    "- Each file is identified by a UUID\n\n"
    "Security guarantees:\n"
    "- No trust in client-provided MIME types\n"
    "- Strict extension-to-MIME validation\n"
    "- Executable content is rejected by default\n"
    "- Atomic write operations prevent partial persistence\n"
)


@router.post(
    "/files",
    status_code=201,
    summary="Upload and persist a file",
    description=post_description,
    response_model=ResponseEnvelope[UploadFileData],
)
async def post_file(
    file: Annotated[UploadFile, File(...)],
    upload_request: Annotated[UploadFileRequest, Depends(parse_post_file_request)],
    request: Request,
) -> JSONResponse | ResponseEnvelope[UploadFileData]:
    """Upload a file, validate it, and persist blob + metadata.

    Args:
        file: Multipart uploaded file stream.
        upload_request: Typed request schema for form fields.
        request: Incoming HTTP request.

    Returns:
        A success response envelope with file metadata, or a JSON error response
        mapped from structured STAR action errors.
    """

    verify_checksum: VerifyChecksumParams | None = None
    if upload_request.checksum:
        try:
            verify_checksum = VerifyChecksumParams(
                expected=upload_request.checksum,
                algorithm="sha256",
            )
        except ValidationError as exc:
            payload = ResponseEnvelope.failure(
                code="INVALID_REQUEST",
                message="Invalid checksum parameter.",
                details={"errors": exc.errors()},
            )
            return JSONResponse(status_code=400, content=payload.model_dump())

    try:
        settings = get_runtime_settings(request)
        metadata = await upload_file_handler(
            file,
            verify_checksum=verify_checksum,
            settings=settings,
        )
        return ResponseEnvelope.success_response(UploadFileData(file=metadata))
    except StarError as exc:
        payload = ResponseEnvelope.failure(
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.http_status, content=payload.model_dump())


get_metadata_description = (
    "**Retrieve metadata for a previously uploaded file managed by STAR.**\n\n"
    "This endpoint provides read-only access to file metadata stored in the STAR "
    "filesystem-backed storage model. It does not return the file contents.\n\n"
    "Behavior:\n"
    "- Validates the provided file identifier (UUID)\n"
    "- Loads metadata from STAR-managed storage\n"
    "- Returns structured metadata in a standard response envelope\n\n"
    "Storage model:\n"
    "- Files are stored as immutable blobs\n"
    "- Metadata is persisted as a JSON sidecar document\n"
    "- Each file is uniquely identified by a UUID\n\n"
    "Security considerations:\n"
    "- No direct filesystem paths are exposed\n"
    "- No file content is returned by this endpoint\n"
    "- Access is controlled via STAR authentication middleware\n\n"
    "Use cases:\n"
    "- Verify upload success\n"
    "- Inspect file properties (size, type, checksum)\n"
    "- Integrate with automation workflows (e.g., n8n)\n"
)


@router.get(
    "/files/{id}",
    summary="Retrieve file metadata",
    description=get_metadata_description,
    response_model=ResponseEnvelope[UploadFileData],
)
async def get_file(
    id: UUID,
    request: Request,
) -> JSONResponse | ResponseEnvelope[UploadFileData]:
    """Retrieve file metadata by UUID.

    Args:
        id: File UUID.
        request: Incoming HTTP request.

    Returns:
        Success envelope with typed file metadata or a structured STAR error response.
    """

    try:
        settings = get_runtime_settings(request)
        metadata = await get_file_metadata_handler(file_id=id, settings=settings)
        return ResponseEnvelope.success_response(UploadFileData(file=metadata))
    except StarError as exc:
        payload = ResponseEnvelope.failure(
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.http_status, content=payload.model_dump())


list_files_description = (
    "**List STAR-managed files with cursor pagination and deterministic ordering.**\n\n"
    "Supports filtering by `status`, `mime_type`, and `extension`, with "
    "sorting by `created_at` and stable tiebreaking by file id."
)


@router.get(
    "/files",
    summary="List stored files",
    description=list_files_description,
    response_model=ResponseEnvelope[FileListData],
)
async def list_files(
    request: Request,
    limit: int = 20,
    cursor: str | None = None,
    sort: str = "created_at",
    order: str = "asc",
    status: str | None = None,
    mime_type: str | None = None,
    extension: str | None = None,
) -> JSONResponse | ResponseEnvelope[FileListData]:
    """List persisted files using cursor pagination."""

    try:
        settings = get_runtime_settings(request)
        result = await list_files_handler(
            limit=limit,
            cursor=cursor,
            sort=sort,
            order=order,
            status=status,
            mime_type=mime_type,
            extension=extension,
            settings=settings,
        )
        return ResponseEnvelope.success_response(result)
    except StarError as exc:
        payload = ResponseEnvelope.failure(
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.http_status, content=payload.model_dump())


get_content_description = (
    "**Stream the binary contents of a previously uploaded file managed by STAR.**\n\n"
    "This endpoint resolves the file through STAR metadata, validates that the "
    "file is in ready state, and returns the blob as a streamed download "
    "response.\n\n"
    "Behavior:\n"
    "- Validates the provided file identifier (UUID)\n"
    "- Loads metadata from STAR-managed storage\n"
    "- Verifies that the file is available for download\n"
    "- Streams the file content without loading the entire blob into memory\n\n"
    "Security considerations:\n"
    "- No direct filesystem paths are exposed\n"
    "- Only STAR-managed file identifiers are accepted\n"
    "- Access is controlled via STAR authentication middleware\n"
)


@router.get(
    "/files/{id}/content",
    summary="Download file content",
    description=get_content_description,
    response_model=None,  # Response is a streamed binary, not JSON
    responses={
        200: {
            "description": "Streamed file content.",
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
            "headers": {
                "Content-Disposition": {
                    "description": "Download filename",
                    "schema": {"type": "string"},
                },
                "Content-Length": {
                    "description": "Size of the file in bytes",
                    "schema": {"type": "integer"},
                },
            },
        }
    },
)
async def get_file_content(id: UUID, request: Request):
    """Stream file content by UUID."""

    try:
        settings = get_runtime_settings(request)
        descriptor = await get_file_content_handler(file_id=id, settings=settings)

        headers = {
            "Content-Disposition": f'attachment; filename="{descriptor.filename}"',
        }

        if descriptor.size_bytes is not None:
            headers["Content-Length"] = str(descriptor.size_bytes)

        return StreamingResponse(
            iter_file_chunks(descriptor.blob_path),
            media_type=descriptor.mime_type,
            headers=headers,
        )
    except StarError as exc:
        payload = ResponseEnvelope.failure(
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.http_status, content=payload.model_dump())


delete_description = (
    "**Delete a previously uploaded file managed by STAR.**\n\n"
    "This endpoint resolves the file through STAR metadata, removes the "
    "public metadata record first, and then cleans the blob artifact "
    "best-effort. If valid metadata exists but the blob is already missing, "
    "the request is treated as successful cleanup. Deletion is only allowed "
    "when file metadata is in `ready` state."
)


@router.delete(
    "/files/{id}",
    summary="Delete a stored file",
    description=delete_description,
    response_model=ResponseEnvelope[DeleteFileData],
)
async def delete_file(
    id: UUID,
    request: Request,
) -> JSONResponse | ResponseEnvelope[DeleteFileData]:
    """Delete file metadata and clean its blob by UUID."""

    try:
        settings = get_runtime_settings(request)
        result = await delete_file_handler(file_id=id, settings=settings)
        return ResponseEnvelope.success_response(DeleteFileData(file=result))
    except StarError as exc:
        payload = ResponseEnvelope.failure(
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.http_status, content=payload.model_dump())
