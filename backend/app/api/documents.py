from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.models.document import DocumentRecord, DocumentUploadResponse
from app.models.read_api import (
    DocumentPublicResponse,
    DocumentSummaryPublicResponse,
    PagePublicResponse,
)
from app.services.storage import StorageService, get_storage_service


router = APIRouter(prefix="/api/documents", tags=["documents"])


def _is_pdf_signature(file_bytes: bytes) -> bool:
    return file_bytes.startswith(b"%PDF-")


def _has_pdf_hint(upload_file: UploadFile) -> bool:
    filename = upload_file.filename or ""
    return filename.lower().endswith(".pdf") or upload_file.content_type == "application/pdf"


def _get_document_or_404(storage: StorageService, document_id: str) -> DocumentRecord:
    document = storage.get_document(document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return document


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


@router.get("/{document_id}", response_model=DocumentPublicResponse)
def get_document(
    document_id: str,
    storage: StorageService = Depends(get_storage_service),
) -> DocumentPublicResponse:
    document = _get_document_or_404(storage, document_id)
    return DocumentPublicResponse(
        document_id=document.document_id,
        filename=document.filename,
        status=document.status,
        total_pages=document.total_pages,
    )


@router.get("/{document_id}/summary", response_model=DocumentSummaryPublicResponse)
def get_document_summary(
    document_id: str,
    storage: StorageService = Depends(get_storage_service),
) -> DocumentSummaryPublicResponse:
    _get_document_or_404(storage, document_id)

    try:
        summary_artifact = storage.load_document_summary(document_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored artifact is invalid.",
        ) from exc

    if summary_artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document summary not found.",
        )

    return DocumentSummaryPublicResponse(**summary_artifact["result"])


@router.get("/{document_id}/pages/{page_number}", response_model=PagePublicResponse)
def get_page_result(
    document_id: str,
    page_number: int,
    request: Request,
    storage: StorageService = Depends(get_storage_service),
) -> PagePublicResponse:
    document = _get_document_or_404(storage, document_id)
    if document.total_pages is not None and (page_number < 1 or page_number > document.total_pages):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found.",
        )

    page = storage.get_page(document_id, page_number)
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found.",
        )

    try:
        pass2_artifact = storage.load_pass2_result(document_id, page_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored artifact is invalid.",
        ) from exc

    if pass2_artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page result not found.",
        )

    image_path = storage.resolve_relative_path(page.image_path)
    if not image_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Rendered page image is unavailable.",
        )

    try:
        image_subpath = storage.get_rendered_image_subpath(page.image_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Rendered page image is unavailable.",
        ) from exc

    image_url = str(request.url_for("rendered-pages", path=image_subpath))
    result = pass2_artifact["result"]
    return PagePublicResponse(
        image_url=image_url,
        **result,
    )
