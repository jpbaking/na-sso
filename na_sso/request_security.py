"""Browser request-boundary protections and baseline response headers."""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _normalised_origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.lower(), port


def _csrf_response(request: Request) -> Response:
    message = "Cross-site state-changing requests are not accepted."
    if request.url.path.startswith("/api/v1"):
        return JSONResponse(
            {"api_version": "v1", "data": None, "error": {
                "code": "cross_site_request", "message": message,
            }},
            status_code=403,
        )
    return PlainTextResponse(message, status_code=403)


async def browser_request_boundary(request: Request, call_next) -> Response:
    """Reject cross-site mutations without disrupting non-browser API clients."""
    if request.method.upper() not in SAFE_METHODS:
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site == "cross-site":
            return _with_security_headers(request, _csrf_response(request))

        expected = _normalised_origin(str(request.base_url))
        supplied = request.headers.get("origin")
        referer = request.headers.get("referer")
        if supplied and _normalised_origin(supplied) != expected:
            return _with_security_headers(request, _csrf_response(request))
        if not supplied and referer and _normalised_origin(referer) != expected:
            return _with_security_headers(request, _csrf_response(request))

    response = await call_next(request)
    return _with_security_headers(request, response)


def _with_security_headers(request: Request, response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response
