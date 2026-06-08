"""
Authentication API endpoints
"""
import logging
from fastapi import APIRouter, HTTPException, status
from src.schemas.auth import VastLoginRequest, Token, UserInfo
from src.services.auth_service import auth_service, CurrentUser
from src.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=Token)
async def login(request: VastLoginRequest):
    """
    Login with VAST user credentials (username + password).
    VMS and tenant are taken from backend config (not from request).

    Args:
        request: Login request with username and password

    Returns:
        JWT token and user information
    """
    settings = get_settings()
    logger.info(
        "Login attempt for user: %s (VMS: %s, tenant: %s)",
        request.username, settings.vast_host, settings.tenant_name,
    )

    token = auth_service.authenticate_user(request.username, request.password)
    if not token:
        logger.warning("Login failed for user: %s", request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    logger.info("Login successful for user: %s (tenant: %s)", request.username, settings.tenant_name)
    return Token(
        access_token=token,
        token_type="bearer",
        username=request.username,
    )


@router.get("/me", response_model=UserInfo)
async def get_current_user_info(current_user: CurrentUser):
    """
    Get current authenticated user information

    Args:
        current_user: Current authenticated user (from dependency)

    Returns:
        User information
    """
    logger.info("User info request for: %s", current_user.username)

    return UserInfo(
        username=current_user.username,
        email=current_user.email,
        auth_type=current_user.auth_type,
    )
