from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.logs import InteractionLogRequest, InteractionLogResponse
from app.services.log_store import LogStore, get_log_store


router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.post("", response_model=InteractionLogResponse)
def create_interaction_log(
    payload: InteractionLogRequest,
    log_store: LogStore = Depends(get_log_store),
) -> InteractionLogResponse:
    try:
        log_store.append_log(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store interaction log.",
        ) from exc

    return InteractionLogResponse(ok=True)
