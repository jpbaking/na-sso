from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from oneauth import __version__
from oneauth.config import get_settings
from oneauth.db import get_session, init_db

_PKG_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=_PKG_DIR / "templates")


def bootstrap_admin() -> None:
    from oneauth.models import AdminAccount
    from oneauth.security import hash_password

    s = get_settings()
    with get_session() as db:
        if not db.query(AdminAccount).filter(
            AdminAccount.username == s.admin_username
        ).first():
            db.add(
                AdminAccount(
                    username=s.admin_username,
                    password_hash=hash_password(s.admin_bootstrap_password),
                )
            )
            db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_admin()
    yield


app = FastAPI(title="One Auth (Non-SSO)", version=__version__, lifespan=lifespan)

app.mount("/design", StaticFiles(directory=_PKG_DIR / "static" / "design"), name="design")

for _icon in ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "site.webmanifest"):
    app.add_api_route(
        f"/{_icon}",
        (lambda name: (lambda: FileResponse(_PKG_DIR / "static" / name)))(_icon),
        include_in_schema=False,
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "version": __version__}


from oneauth.auth import router as auth_router  # noqa: E402
from oneauth.audit import router as audit_router  # noqa: E402
from oneauth.status import router as status_router  # noqa: E402
from oneauth.users import router as users_router  # noqa: E402

app.include_router(auth_router)
app.include_router(audit_router)
app.include_router(users_router)
app.include_router(status_router)
