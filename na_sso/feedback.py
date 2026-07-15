"""Signed one-time UI feedback shared by redirecting workflows."""

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from na_sso.config import get_settings

FLASH_COOKIE = "na-sso-feedback"
FLASH_MAX_AGE = 300


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="feedback")


def redirect_with_feedback(
    destination: str,
    *,
    title: str,
    message: str,
    level: str = "success",
    status_code: int = 303,
) -> RedirectResponse:
    response = RedirectResponse(destination, status_code=status_code)
    response.set_cookie(
        FLASH_COOKIE,
        _serializer().dumps({
            "title": title,
            "message": message,
            "level": level if level in {"success", "danger", "info"} else "info",
        }),
        max_age=FLASH_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=get_settings().session_cookie_secure,
    )
    return response


def read_feedback(request: Request) -> dict[str, str] | None:
    token = request.cookies.get(FLASH_COOKIE)
    if not token:
        return None
    try:
        value = _serializer().loads(token, max_age=FLASH_MAX_AGE)
    except (BadSignature, KeyError, TypeError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("message"), str):
        return None
    return {
        "title": str(value.get("title", "Update")),
        "message": value["message"],
        "level": value.get("level", "info"),
    }


def template_response(templates, request: Request, name: str, context: dict, **kwargs):
    context.setdefault("feedback", read_feedback(request))
    response = templates.TemplateResponse(request, name, context, **kwargs)
    if request.cookies.get(FLASH_COOKIE):
        response.delete_cookie(FLASH_COOKIE)
    return response
