import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from na_sso import __version__
from na_sso.config import get_settings
from na_sso.db import get_session, init_db

_PKG_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=_PKG_DIR / "templates")


def bootstrap_admin() -> None:
    from na_sso.models import AdminAccount, ManagedUser, utcnow
    from na_sso.security import hash_password

    s = get_settings()
    with get_session() as db:
        legacy = db.query(AdminAccount).filter(AdminAccount.username == s.admin_username).first()
        root = db.query(ManagedUser).filter(ManagedUser.role == "root").one_or_none()
        collision = db.query(ManagedUser).filter(ManagedUser.username == s.admin_username).one_or_none()
        if root is None and collision is None:
            password_hash = legacy.password_hash if legacy else hash_password(s.admin_bootstrap_password)
            db.add(ManagedUser(
                id=0,
                username=s.admin_username,
                display_name="SUPERADMIN",
                password_hash=password_hash,
                role="root",
                password_changed_at=utcnow(),
                status="active",
                desired_action="local_only",
            ))
        elif root is not None:
            root.display_name = "SUPERADMIN"
            root.status = "active"
            root.desired_action = "local_only"
            root.role = "root"
            root.deletion_requested_at = None
            root.deleted_at = None
        if legacy is None:
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
    from na_sso.sync import retry_worker
    from na_sso.audit_retention import audit_retention_worker
    from na_sso.notifications import notification_worker
    from na_sso.reconcile import reconciliation_worker
    from na_sso.governance import governance_worker
    workers = [
        asyncio.create_task(retry_worker()),
        asyncio.create_task(audit_retention_worker()),
        asyncio.create_task(notification_worker()),
        asyncio.create_task(reconciliation_worker()),
        asyncio.create_task(governance_worker()),
    ]
    yield
    for worker in workers:
        worker.cancel()
    for worker in workers:
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="NA-SSO (Not Another SSO)", version=__version__, lifespan=lifespan)

from na_sso.request_security import browser_request_boundary  # noqa: E402

app.middleware("http")(browser_request_boundary)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    if request.url.path.startswith("/api/v1"):
        from na_sso.api_contract import api_error
        details = [{
            "location": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        } for item in error.errors()]
        return api_error(
            request, 422, "validation_error", "The request did not match the API contract.",
            details=details,
        )
    return await request_validation_exception_handler(request, error)


@app.exception_handler(HTTPException)
async def api_http_error(request: Request, error: HTTPException):
    if request.url.path.startswith("/api/v1"):
        from na_sso.api_contract import api_error
        return api_error(request, error.status_code, "http_error", str(error.detail))
    return await http_exception_handler(request, error)

app.mount("/design", StaticFiles(directory=_PKG_DIR / "static" / "design"), name="design")

for _icon in (
    "favicon.svg", "favicon.ico", "apple-touch-icon.png", "site.webmanifest", "app.css"
):
    app.add_api_route(
        f"/{_icon}",
        (lambda name: (lambda: FileResponse(_PKG_DIR / "static" / name)))(_icon),
        include_in_schema=False,
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "version": __version__}


from na_sso.auth import router as auth_router  # noqa: E402
from na_sso.api import router as api_router  # noqa: E402
from na_sso.audit import router as audit_router  # noqa: E402
from na_sso.assignments import router as assignment_router  # noqa: E402
from na_sso.bulk import router as bulk_router  # noqa: E402
from na_sso.mfa import router as mfa_router  # noqa: E402
from na_sso.notifications import router as notification_router  # noqa: E402
from na_sso.reconcile import router as reconciliation_router  # noqa: E402
from na_sso.governance import router as governance_router  # noqa: E402
from na_sso.service_accounts import router as service_account_router  # noqa: E402
from na_sso.ssh_keys import router as ssh_key_router  # noqa: E402
from na_sso.status import router as status_router  # noqa: E402
from na_sso.users import router as users_router  # noqa: E402
from na_sso.unmanaged import router as unmanaged_router  # noqa: E402

app.include_router(auth_router)
app.include_router(api_router)
app.include_router(audit_router)
app.include_router(assignment_router)
app.include_router(bulk_router)
app.include_router(mfa_router)
app.include_router(notification_router)
app.include_router(reconciliation_router)
app.include_router(governance_router)
app.include_router(service_account_router)
app.include_router(ssh_key_router)
app.include_router(users_router)
app.include_router(unmanaged_router)
app.include_router(status_router)
