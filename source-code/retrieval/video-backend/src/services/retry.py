import logging
import time
from typing import Callable, Iterable, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY_SECONDS = 30
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ReadTimeout,
)


def call_with_retry(
    fn: Callable[[], requests.Response],
    *,
    operation: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay_seconds: int = DEFAULT_BASE_DELAY_SECONDS,
    retryable_status_codes: Iterable[int] = RETRYABLE_STATUS_CODES,
) -> requests.Response:
    retryable_status_codes = frozenset(retryable_status_codes)
    last_error: Optional[Exception] = None
    last_response: Optional[requests.Response] = None

    for attempt in range(max_retries + 1):
        try:
            response = fn()
            if response.status_code in retryable_status_codes:
                last_response = response
                last_error = requests.exceptions.HTTPError(
                    f"HTTP {response.status_code} from {operation}",
                    response=response,
                )
            else:
                return response
        except RETRYABLE_EXCEPTIONS as exc:
            last_response = None
            last_error = exc

        if attempt == max_retries:
            break

        delay = base_delay_seconds * (attempt + 1)
        logger.warning(
            "[RETRY] %s attempt=%d/%d err=%s -- retrying in %ds",
            operation, attempt + 1, max_retries + 1, last_error, delay,
        )
        time.sleep(delay)

    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"{operation}: retries exhausted with no error captured")
