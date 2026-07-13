from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from oneauth.config import get_settings

COOKIE = "oneauth_session"
MAX_AGE = 12 * 3600

router = APIRouter()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="session")


def current_admin(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=MAX_AGE)["u"]
    except (BadSignature, KeyError):
        return None


@router.get("/login")
async def login_page(request: Request):
    from oneauth.main import templates

    if current_admin(request):
        return RedirectResponse("/users", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    from oneauth.main import templates

    from oneauth.db import get_session
    from oneauth.models import AdminAccount
    from oneauth.security import verify_password

    with get_session() as db:
        admin = (
            db.query(AdminAccount).filter(AdminAccount.username == username).one_or_none()
        )
    if admin and verify_password(password, admin.password_hash):
        resp = RedirectResponse("/users", status_code=303)
        resp.set_cookie(
            COOKIE,
            _serializer().dumps({"u": username}),
            max_age=MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return resp
    return templates.TemplateResponse(
        request, "login.html", {"error": "Invalid credentials."}, status_code=401
    )


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp
