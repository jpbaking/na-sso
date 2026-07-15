from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from na_sso.config import get_settings

COOKIE = "na-sso-session"
MAX_AGE = 12 * 3600

router = APIRouter()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="session")


def current_admin(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE)
    except (BadSignature, KeyError):
        return None
    from na_sso.db import get_session
    from na_sso.models import ManagedUser

    if "id" not in data:  # pre-migration cookie; force a fresh authenticated session
        return None
    with get_session() as db:
        account = db.get(ManagedUser, data["id"])
        if (
            not account
            or account.status != "active"
            or account.desired_action == "delete"
            or account.session_version != data.get("v")
            or account.role not in {"admin", "root"}
        ):
            return None
        return account.username


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE)
    except (BadSignature, KeyError):
        return None
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    with get_session() as db:
        account = db.get(ManagedUser, data.get("id"))
        if not account or account.status != "active" or account.session_version != data.get("v"):
            return None
        return {"id": account.id, "username": account.username, "role": account.role,
                "restricted": account.password_decision_required}


@router.get("/login")
async def login_page(request: Request):
    from na_sso.main import templates

    if current_admin(request):
        return RedirectResponse("/users", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    from na_sso.main import templates

    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import verify_password

    with get_session() as db:
        admin = (
            db.query(ManagedUser).filter(ManagedUser.username == username).one_or_none()
        )
    if admin and admin.password_hash and admin.status == "active" and verify_password(password, admin.password_hash):
        from na_sso.sync import credential_handoff
        await credential_handoff(admin.id, password)
        destination = "/account/password-decision" if admin.password_decision_required else ("/users" if admin.role in {"admin", "root"} else "/account")
        resp = RedirectResponse(destination, status_code=303)
        resp.set_cookie(
            COOKIE,
            _serializer().dumps({"id": admin.id, "v": admin.session_version}),
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


def _password_form(title: str, *, decision: bool = False, error: str = "") -> HTMLResponse:
    keep = '<button name="choice" value="keep">Keep current password and acknowledge risk</button>' if decision else ""
    policy = get_settings().file.password_policy
    return HTMLResponse(f"""<!doctype html><title>{title}</title><h1>{title}</h1>
<p role="alert">{error}</p><form method="post"><label>Current password <input type="password" name="current_password"></label>
<label>New password <input id="new-password" type="password" name="new_password"></label>
<button id="generate-password" type="button">Generate</button><div id="password-checks" aria-live="polite"></div>
<p>Password history is checked securely on submission.</p>
<button name="choice" value="change">Change password</button>{keep}</form>
<script>const p=document.getElementById('new-password'),o=document.getElementById('password-checks');
function check(){{const v=p.value,r=[['minimum length',v.length>={policy.min_length}],['lowercase',/[a-z]/.test(v)],['uppercase',/[A-Z]/.test(v)],['digit',/\\d/.test(v)],['symbol',/[^A-Za-z0-9]/.test(v)]];o.textContent=r.map(x=>x[0]+': '+(x[1]?'passed':'not yet')).join(' · ')}}
p.oninput=check;document.getElementById('generate-password').onclick=()=>{{const c='abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#%+',b=crypto.getRandomValues(new Uint8Array({max(16, policy.min_length)-4}));p.value=[...b].map(x=>c[x%c.length]).join('')+'aA1!';check()}};check();</script>""")


@router.get("/account")
async def account_page(request: Request):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["restricted"]:
        return RedirectResponse("/account/password-decision", status_code=303)
    return HTMLResponse(f"""<!doctype html><h1>Account</h1><p>{account['username']}</p>
<a href='/account/password'>Change password</a><h2>SSH key</h2>
<button id="generate-key" type="button">Generate key in this browser</button>
<form id="key-form" method="post" action="/account/ssh-key"><input type="hidden" name="public_key" id="public-key"></form>
<form method="post" action="/account/ssh-key/generate"><button>Compatibility fallback: server-handled, non-persistent, one-time key</button></form>
<script>
const u32=n=>new Uint8Array([(n>>>24)&255,(n>>>16)&255,(n>>>8)&255,n&255]);
const join=(...a)=>{{const o=new Uint8Array(a.reduce((n,x)=>n+x.length,0));let p=0;for(const x of a){{o.set(x,p);p+=x.length}}return o}};
const b64=b=>btoa(String.fromCharCode(...b));
document.getElementById('generate-key').onclick=async()=>{{
 try{{const keys=await crypto.subtle.generateKey({{name:'Ed25519'}},true,['sign','verify']);
 const raw=new Uint8Array(await crypto.subtle.exportKey('raw',keys.publicKey));
 const label=new TextEncoder().encode('ssh-ed25519');const blob=join(u32(label.length),label,u32(raw.length),raw);
 document.getElementById('public-key').value='ssh-ed25519 '+b64(blob);
 const pkcs8=new Uint8Array(await crypto.subtle.exportKey('pkcs8',keys.privateKey));
 const pem='-----BEGIN PRIVATE KEY-----\\n'+b64(pkcs8).match(/.{{1,64}}/g).join('\\n')+'\\n-----END PRIVATE KEY-----\\n';
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([pem],{{type:'application/x-pem-file'}}));a.download='na-sso_ed25519';a.click();URL.revokeObjectURL(a.href);
 document.getElementById('key-form').submit();}}catch(e){{alert('Browser key generation is unavailable; use the accurately labeled compatibility fallback if enabled.')}}
}};
</script>""")


@router.get("/account/password-decision")
async def password_decision_page(request: Request):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if not account["restricted"]:
        return RedirectResponse("/account", status_code=303)
    return _password_form("Accept or change your password", decision=True)


@router.post("/account/password-decision")
async def password_decision(request: Request, choice: str = Form(...), current_password: str = Form(""), new_password: str = Form("")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    return _complete_password_action(account, choice, current_password, new_password, decision=True)


@router.get("/account/password")
async def password_page(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=303)
    return _password_form("Change password")


@router.post("/account/password")
async def password_change(request: Request, current_password: str = Form(...), new_password: str = Form(...), choice: str = Form("change")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    return _complete_password_action(account, "change", current_password, new_password, decision=False)


@router.post("/account/ssh-key")
async def enroll_ssh_key(request: Request, public_key: str = Form(""), private_key: str = Form("")):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    if account["role"] == "root":
        return HTMLResponse("Root is local-only.", status_code=403)
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import public_key_from_private
    try:
        derived = public_key_from_private(private_key) if private_key else public_key.strip()
    except (TypeError, ValueError):
        return HTMLResponse("Invalid private key.", status_code=422)
    if not derived.startswith(("ssh-ed25519 ", "ssh-rsa ")):
        return HTMLResponse("Unsupported SSH public key.", status_code=422)
    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        user.ssh_public_key = derived
        record_audit(db, user.username, "ssh_key.enrolled", user.username, derived.split()[0])
        db.commit()
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    return RedirectResponse("/account", status_code=303)


@router.post("/account/ssh-key/generate")
async def server_generate_ssh_key(request: Request):
    account = current_user(request)
    if not account:
        return RedirectResponse("/login", status_code=303)
    from na_sso.config import get_settings
    if account["role"] == "root" or not get_settings().file.ssh_key_policy.allow_server_fallback:
        return HTMLResponse("Server-handled fallback is disabled.", status_code=403)
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import ManagedUser
    from na_sso.security import generate_ssh_keypair
    private, public = generate_ssh_keypair()
    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        user.ssh_public_key = public
        record_audit(db, user.username, "ssh_key.generated_once", user.username, "ed25519 server-handled non-persistent")
        db.commit()
    from na_sso.sync import sync_user
    await sync_user(account["id"], actor=account["username"])
    return HTMLResponse(f"<!doctype html><h1>Save this private key now</h1><p>It is shown once and is not persisted.</p><textarea readonly>{private}</textarea>")


def _complete_password_action(account: dict, choice: str, current_password: str, new_password: str, *, decision: bool):
    from na_sso.audit import record_audit
    from na_sso.db import get_session
    from na_sso.models import ManagedUser, PasswordHistory, utcnow
    from na_sso.security import hash_password, validate_password, verify_password
    from na_sso.config import get_settings

    with get_session() as db:
        user = db.get(ManagedUser, account["id"])
        if not user or not user.password_hash or not verify_password(current_password, user.password_hash):
            return _password_form("Accept or change your password" if decision else "Change password", decision=decision, error="Invalid current password.")
        if choice == "keep" and decision:
            user.password_decision_required = False
            user.password_changed_at = utcnow()
            record_audit(db, user.username, "password.keep_acknowledged", user.username, "risk warning acknowledged")
        elif choice == "change":
            history_rows = db.query(PasswordHistory).filter_by(user_id=user.id).order_by(PasswordHistory.created_at.desc()).limit(get_settings().file.password_policy.history_size).all()
            validation = validate_password(new_password, username=user.username, email=user.email,
                display_name=user.display_name, old_password=current_password,
                history_hashes=tuple([user.password_hash, *(row.password_hash for row in history_rows)]))
            if not validation.valid:
                return _password_form("Accept or change your password" if decision else "Change password", decision=decision, error=" ".join(validation.errors))
            db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
            user.password_hash = hash_password(new_password)
            user.password_decision_required = False
            user.password_changed_at = utcnow()
            user.session_version += 1
            record_audit(db, user.username, "password.changed", user.username)
        else:
            return _password_form("Accept or change your password", decision=True, error="Choose keep or change.")
        db.commit()
        user_id = user.id
    import asyncio
    from na_sso.sync import credential_handoff
    asyncio.create_task(credential_handoff(user_id, new_password if choice == "change" else current_password))
    response = RedirectResponse("/login" if choice == "change" else "/account", status_code=303)
    if choice == "change":
        response.delete_cookie(COOKIE)
    return response
