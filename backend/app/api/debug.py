from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.services.storage import StorageService, get_storage_service


router = APIRouter(prefix="/api/documents", tags=["debug"])


@router.get("/{document_id}/debug/pass1/{page_number}")
def get_pass1_debug_result(
    document_id: str,
    page_number: int,
    storage: StorageService = Depends(get_storage_service),
) -> dict[str, object]:
    try:
        payload = storage.load_pass1_result(document_id, page_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored pass1 artifact is invalid: {exc}",
        ) from exc

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pass1 result was not found for the requested page.",
        )

    return payload


@router.get("/{document_id}/debug/summary")
def get_document_summary_debug_result(
    document_id: str,
    storage: StorageService = Depends(get_storage_service),
) -> dict[str, object]:
    try:
        payload = storage.load_document_summary(document_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored document summary artifact is invalid: {exc}",
        ) from exc

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document summary result was not found for the requested document.",
        )

    return payload


@router.get("/{document_id}/debug/pass2/{page_number}")
def get_pass2_debug_result(
    document_id: str,
    page_number: int,
    storage: StorageService = Depends(get_storage_service),
) -> dict[str, object]:
    try:
        payload = storage.load_pass2_result(document_id, page_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored pass2 artifact is invalid: {exc}",
        ) from exc

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pass2 result was not found for the requested page.",
        )

    return payload
