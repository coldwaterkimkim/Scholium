from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.models.document import DocumentUploadResponse
from app.services.storage import StorageService, get_storage_service


router = APIRouter(prefix="/api/documents", tags=["documents"])


def _is_pdf_signature(file_bytes: bytes) -> bool:
    return file_bytes.startswith(b"%PDF-")


def _has_pdf_hint(upload_file: UploadFile) -> bool:
    filename = upload_file.filename or ""
    return filename.lower().endswith(".pdf") or upload_file.content_type == "application/pdf"


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage_service),
) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDF filename is required.",
        )

    try:
        file_bytes = await file.read()
    finally:
        await file.close()

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if not _has_pdf_hint(file) and not _is_pdf_signature(file_bytes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF uploads are supported.",
        )

    if not _is_pdf_signature(file_bytes):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PDF.",
        )

    sanitized_filename = Path(file.filename).name

    try:
        document_record = storage.save_uploaded_document(
            filename=sanitized_filename,
            file_bytes=file_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store the uploaded document.",
        ) from exc

    return DocumentUploadResponse(
        document_id=document_record.document_id,
        status=document_record.status,
    )
