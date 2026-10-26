"""Live VastDB data dashboard API."""
import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.services.auth_service import CurrentUser
from src.services.model_probe import probe_model_endpoints
from src.services.vastdb_service import get_vastdb_service
from src.schemas.dashboard import DashboardStatsResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    current_user: CurrentUser,
    scope: str = Query(default="all", pattern="^(all|mine|public)$"),
):
    """
    Aggregate statistics for rows in the VastDB collection visible to the current user.

    Includes overview counts, indexed model measures, a live reasoner and embed probe,
    detected objects, upload metadata, upload timeline, ingest quality, and recent videos.
    """
    try:
        vastdb = get_vastdb_service()
        stats = vastdb.get_dashboard_stats(current_user, scope=scope)
        try:
            stats["live_endpoints"] = probe_model_endpoints()
        except Exception as exc:
            logger.warning("[DASHBOARD] live endpoint probe skipped: %s", exc)
        return DashboardStatsResponse(**stats)
    except Exception as exc:
        logger.error("[DASHBOARD] Unexpected failure building stats: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load dashboard stats: {exc}",
        ) from exc
