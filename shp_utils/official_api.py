"""Shared helpers for fetching and parsing official TWSE/TPEx OpenAPI data.

Generic HTTP-fetch-with-retry and defensive value parsing, factored out of the
official daily price fetcher so any future officially-sourced daily feed can
reuse it without re-implementing retry/backoff or ROC-date/number parsing.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlsplit

import requests
import truststore

from shp_utils.official_parsing import parse_int, parse_number, parse_roc_compact_date

# Some official .tw endpoints (notably TPEx) serve a certificate chain missing the
# Subject Key Identifier extension, which OpenSSL 3.x's stricter RFC 5280 chain
# validation rejects even though the chain is otherwise valid (browsers and the OS
# trust store tolerate it). Routing verification through the OS trust store instead
# of the bundled OpenSSL verifier resolves this without weakening verification.
truststore.inject_into_ssl()

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "StockResearchBot/0.1 local research"
DEFAULT_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_RETRY_SLEEP = 60.0
# Fraction of the computed backoff added as random jitter, so retries started by
# one failure do not all re-converge on the same instant.
RETRY_JITTER_FRACTION = 0.25
# Statuses worth asking again for. 429 and the 5xx family are transient by
# definition; 520-524 are the edge-proxy codes the TPEx front end emits when its
# origin misbehaves, which is the shape every observed TPEx failure has taken.
# Any other 4xx describes the request, so repeating it only spends the retry
# budget a genuinely transient failure will need later.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 509, 520, 521, 522, 523, 524, 525, 527, 530})
T = TypeVar("T")


class ResponseTooLargeError(RuntimeError):
    """Raised without retry when an official response exceeds its byte budget."""


class UnexpectedResponseHostError(RuntimeError):
    """The response did not come back from the host the caller asked for.

    A stored ``source_url`` built before the request proves only what the
    program meant to ask for. Following a cross-host redirect would let content
    from somewhere else be filed as official evidence, and no amount of
    validation on the requested URL can detect that after the fact -- so the
    check belongs on the URL the response actually carries.
    """

    def __init__(self, message: str, *, requested_url: str, final_url: str) -> None:
        super().__init__(message)
        self.requested_url = requested_url
        self.final_url = final_url


class OfficialFetchError(RuntimeError):
    """A fetch that gave up, carrying what the endpoint last answered.

    The status code used to survive only inside a formatted message, so a caller
    could not tell a rate-limited 429 from a read timeout and nothing durable
    recorded which had happened. A caller that persists a failure checkpoint
    needs both, so they are attributes rather than prose.
    """

    def __init__(self, message: str, *, status_code: int | None = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


def fetch_bytes_with_retry_url(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    retry_sleep: float = 5.0,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP,
    timeout: float = 30,
    headers: dict[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> tuple[bytes, str]:
    """GET bounded raw bytes and return them with the resolved request URL."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    return _fetch_with_retry(
        url,
        parser=lambda response: _read_limited_bytes(response, max_bytes=max_bytes),
        params=params,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
        timeout=timeout,
        headers=headers,
        stream=True,
    )


def fetch_json_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    retry_sleep: float = 5.0,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP,
    timeout: float = 30,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET a URL and parse its JSON body, retrying on request/parse failure
    with jittered exponential backoff capped at ``max_retry_sleep``."""
    payload, _url = fetch_json_with_retry_url(
        url,
        params=params,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
        timeout=timeout,
        headers=headers,
    )
    return payload


def fetch_json_with_retry_url(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    retry_sleep: float = 5.0,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP,
    timeout: float = 30,
    headers: dict[str, str] | None = None,
    expected_host: str | None = None,
) -> tuple[Any, str]:
    """As :func:`fetch_json_with_retry`, but also returns the resolved request
    URL, for callers that record which query produced a stored payload.

    The GET is inside the retried block. It used to sit outside it, so the
    retries only ever covered a bad status code or an unparseable body -- the
    transport failures they exist for (read timeout, connection reset) escaped
    on the first attempt. On 2026-08-19 a single 30-second TWSE read timeout
    took down a whole pipeline step that was configured for three retries.
    """
    return _fetch_with_retry(
        url,
        parser=lambda response: response.json(),
        params=params,
        retries=retries,
        retry_sleep=retry_sleep,
        max_retry_sleep=max_retry_sleep,
        timeout=timeout,
        headers=headers,
        stream=False,
        expected_host=expected_host,
    )


def _fetch_with_retry(
    url: str,
    *,
    parser: Callable[[requests.Response], T],
    params: dict[str, Any] | None,
    retries: int,
    retry_sleep: float,
    timeout: float,
    headers: dict[str, str] | None,
    stream: bool,
    max_retry_sleep: float = DEFAULT_MAX_RETRY_SLEEP,
    expected_host: str | None = None,
) -> tuple[T, str]:
    request_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        request_headers.update(headers)
    last_error: Exception | None = None
    attempts = max(retries, 0) + 1
    for attempt in range(1, attempts + 1):
        response: requests.Response | None = None
        status_code: int | None = None
        try:
            request_kwargs: dict[str, Any] = {
                "params": params,
                "timeout": timeout,
                "headers": request_headers,
            }
            if stream:
                request_kwargs["stream"] = True
            response = requests.get(url, **request_kwargs)
            status_code = response.status_code
            response.raise_for_status()
            if expected_host is not None:
                _require_response_host(response, expected_host=expected_host)
            return parser(response), response.url
        except UnexpectedResponseHostError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if _is_permanent_status(status_code):
                raise OfficialFetchError(
                    f"Failed to fetch JSON from {url}; status={status_code} is not retryable",
                    status_code=status_code,
                    attempts=attempt,
                ) from exc
            if attempt >= attempts:
                raise OfficialFetchError(
                    f"Failed to fetch JSON from {url}; {_failure_detail(response)}",
                    status_code=status_code,
                    attempts=attempt,
                ) from exc
            delay = _retry_delay(
                response,
                attempt=attempt,
                retry_sleep=retry_sleep,
                max_retry_sleep=max_retry_sleep,
            )
            logger.warning(
                "fetch attempt %d/%d for %s failed (%s); retrying in %.1fs",
                attempt,
                attempts,
                url,
                exc,
                delay,
            )
            time.sleep(delay)
    raise OfficialFetchError(f"Failed to fetch {url}", attempts=attempts) from last_error


def _require_response_host(response: requests.Response, *, expected_host: str) -> None:
    """Reject a response that did not come from ``expected_host`` over https.

    Hostname comparison is exact equality after case folding, never a suffix or
    substring test -- ``www.twse.com.tw.example.net`` ends with the expected
    name and is a different party. Userinfo, a non-default port and a fragment
    are all rejected rather than normalised away, because each is a way to make
    a URL read as one host while resolving somewhere else.
    """
    final_url = str(response.url)
    parsed = urlsplit(final_url)
    problem: str | None = None
    if parsed.scheme.lower() != "https":
        problem = f"scheme {parsed.scheme!r}"
    elif parsed.username or parsed.password:
        problem = "embedded credentials"
    elif parsed.port is not None:
        problem = f"non-default port {parsed.port}"
    elif parsed.fragment:
        problem = "a fragment"
    elif (parsed.hostname or "").lower() != expected_host.lower():
        problem = f"host {parsed.hostname!r}"
    if problem is None:
        return
    raise UnexpectedResponseHostError(
        f"response for {expected_host} came back with {problem} ({final_url})",
        requested_url=str(getattr(response.request, "url", "") or ""),
        final_url=final_url,
    )


def _is_permanent_status(status_code: int | None) -> bool:
    """A 4xx outside the retryable set describes the request rather than the
    server's mood, so repeating it verbatim can only fail the same way."""
    return status_code is not None and 400 <= status_code < 500 and status_code not in RETRYABLE_STATUS_CODES


def _retry_delay(
    response: requests.Response | None,
    *,
    attempt: int,
    retry_sleep: float,
    max_retry_sleep: float,
) -> float:
    """Seconds to wait before the next attempt.

    An explicit ``Retry-After`` wins, because it is the server stating how long
    it wants; otherwise back off exponentially with jitter. The previous linear
    ``retry_sleep * attempt`` gave up after about 30 seconds on the default
    settings, far short of the cooldown TPEx has been observed to need.
    """
    retry_after = _retry_after_seconds(response)
    if retry_after is not None:
        return min(retry_after, max_retry_sleep)
    # Cap after the jitter, not before it. Capping the base first let a 300s
    # setting sleep up to 375s, so the parameter did not mean what its name
    # said and no caller could bound a run from it.
    base = min(retry_sleep * (2 ** (attempt - 1)), max_retry_sleep)
    return min(base + random.uniform(0.0, base * RETRY_JITTER_FRACTION), max_retry_sleep)


def _retry_after_seconds(response: requests.Response | None) -> float | None:
    """Read a numeric ``Retry-After``. The HTTP-date form is ignored rather than
    guessed at, because a skewed clock would turn it into an unbounded sleep."""
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _failure_detail(response: requests.Response | None) -> str:
    """Describe what came back, for the final give-up message.

    A transport failure has no response at all, so there is no status or body to
    quote; the chained cause carries the real error in that case.
    """
    if response is None:
        return "no response (the request itself failed)"
    # Do not force a streamed response body into memory merely to format an
    # error. The chained exception retains the parse/transport detail.
    return f"status={response.status_code}"


def _read_limited_bytes(response: requests.Response, *, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = None
        if declared_size is not None and declared_size > max_bytes:
            raise ResponseTooLargeError(f"response exceeds {max_bytes} byte limit")

    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        if len(body) + len(chunk) > max_bytes:
            raise ResponseTooLargeError(f"response exceeds {max_bytes} byte limit")
        body.extend(chunk)
    return bytes(body)


__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_RETRY_SLEEP",
    "DEFAULT_USER_AGENT",
    "OfficialFetchError",
    "RETRYABLE_STATUS_CODES",
    "ResponseTooLargeError",
    "UnexpectedResponseHostError",
    "fetch_bytes_with_retry_url",
    "fetch_json_with_retry",
    "fetch_json_with_retry_url",
    "parse_int",
    "parse_number",
    "parse_roc_compact_date",
]
