"""
Authentication service for VAST system integration
"""
import logging
import time
import urllib3
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, Optional

import jwt
import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import api_key

from src.config import get_settings
from src.models.user import User
from src.services.retry import call_with_retry

logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

auth_scheme = api_key.APIKeyHeader(name="Authorization", auto_error=False)

VMS_TIMEOUT_SECONDS = 15


class AuthService:
    def __init__(self) -> None:
        settings = get_settings()
        self.vms_host = settings.vast_host
        if self.vms_host and not self.vms_host.startswith(("http://", "https://")):
            self.vms_host = f"https://{self.vms_host}"

        self.tenant_name = settings.tenant_name or "default"
        self.jwt_secret = settings.jwt_secret
        if not self.jwt_secret:
            raise RuntimeError("jwt_secret is not configured -- refusing to start")

    def authenticate_user(self, username: str, password: str) -> Optional[str]:
        if not self.vms_host:
            logger.warning("[AUTH] vast_host not configured -- bypassing authentication for %s", username)
            return self._create_jwt(username, {})

        candidate_urls = []
        if self.tenant_name:
            candidate_urls.append(f"{self.vms_host}/api/token/{self.tenant_name}/")
        candidate_urls.append(f"{self.vms_host}/api/token/")

        last_status = None
        last_body = ""
        for url in candidate_urls:
            ok, status_code, body = self._post_token(url, username, password)
            last_status, last_body = status_code, body
            if ok:
                logger.info("[AUTH] login ok user=%s url=%s", username, url)
                return self._create_jwt(username, {"role": "user"})
            logger.info(
                "[AUTH] login attempt failed user=%s url=%s status=%s body=%s",
                username, url, status_code, body[:160],
            )

        logger.warning(
            "[AUTH] login failed user=%s status=%s body=%s",
            username, last_status, last_body[:200],
        )
        return None

    def verify_token(self, token: str) -> User:
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        username = payload.get("sub")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return User(
            username=username,
            auth_type=payload.get("role", "user"),
            token_claims=payload,
        )

    def _post_token(self, url: str, username: str, password: str) -> tuple[bool, Optional[int], str]:
        t0 = time.perf_counter()
        try:
            resp = call_with_retry(
                lambda: requests.post(
                    url,
                    json={"username": username, "password": password},
                    verify=False,
                    timeout=VMS_TIMEOUT_SECONDS,
                ),
                operation=f"AUTH:vms_token:{url}",
            )
        except requests.RequestException as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "[AUTH] vms exception url=%s elapsed_ms=%d err=%s",
                url, elapsed_ms, e,
            )
            return False, None, str(e)

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if resp.status_code == 200:
            logger.info("[AUTH] vms ok url=%s elapsed_ms=%d", url, elapsed_ms)
            return True, resp.status_code, ""
        if resp.status_code in (401, 403):
            return False, resp.status_code, resp.text or ""
        logger.warning(
            "[AUTH] vms non-retryable http=%d elapsed_ms=%d body=%s",
            resp.status_code, elapsed_ms, (resp.text or "")[:160],
        )
        return False, resp.status_code, resp.text or ""

    def _create_jwt(self, username: str, extra_claims: Optional[Dict[str, Any]] = None) -> str:
        now = datetime.now(timezone.utc)
        payload: Dict[str, Any] = {
            "sub": username,
            "exp": now + timedelta(days=1),
            "iat": now,
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")


auth_service = AuthService()


def _extract_bearer_token(authorization: Optional[str]) -> str:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not authorization:
        logger.error("No authorization header provided")
        raise credentials_exception

    token = authorization
    if authorization.lower().startswith("bearer "):
        token = authorization[7:]

    if not token:
        logger.error("Empty token after processing")
        raise credentials_exception

    return token


async def get_current_user(authorization: Annotated[Optional[str], Depends(auth_scheme)] = None) -> User:
    token = _extract_bearer_token(authorization)
    return auth_service.verify_token(token)


async def get_current_user_from_token(token: str) -> User:
    if not token:
        raise Exception("No token provided")
    return auth_service.verify_token(token)


CurrentUser = Annotated[User, Depends(get_current_user)]
