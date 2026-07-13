# One Auth (Non-SSO)

One Auth is a small administrative web app for maintaining the same local
username and password across OPNsense, Nexus Repository, and Nextcloud. It is
not an identity provider and does not provide single sign-on: it calls each
target's user-management API and records the result independently.

## What it does

- Provides a session-protected admin UI for creating, editing, disabling, and
  deleting managed users.
- Generates passwords or accepts operator-supplied passwords.
- Propagates user changes to every enabled target in a background task.
- Shows per-user, per-target pending, successful, and failed states.
- Supports retrying one failed target without repeating successful targets.
- Records admin actions and connector results in an audit log.

The SQLite database stores admin passwords as bcrypt hashes. A managed user's
plaintext password is never stored. While propagation is pending, the password
is encrypted with Fernet using a key derived from `ONEAUTH_SECRET_KEY`; the
encrypted value is removed after every enabled target succeeds.

## Architecture

The FastAPI application serves Jinja templates and a bundled lazyway.io design
kit. SQLAlchemy stores managed users, target sync states, and audit events in a
SQLite database on the `oneauth-data` volume. Connector implementations use
`httpx` to call:

- OPNsense: `/api/auth/user/*`
- Nexus Repository: `/service/rest/v1/security/users/*`
- Nextcloud: `/ocs/v1.php/cloud/users/*`

Operations run after the HTTP response as FastAPI background tasks. This is
suited to a single application process. For higher availability or multiple
workers, move propagation jobs to a durable external queue before scaling out.

## Configuration

Copy the example and replace every placeholder before starting the app:

```sh
cp .config/.env.example .config/.env
```

Core variables:

| Variable | Purpose |
| --- | --- |
| `ONEAUTH_SECRET_KEY` | Long random value used for session signing and pending-password encryption. Keep it stable and backed up. |
| `ONEAUTH_ADMIN_USERNAME` | Local One Auth administrator username. |
| `ONEAUTH_ADMIN_BOOTSTRAP_PASSWORD` | Creates the initial admin when the database has no admin. Changing it later does not rotate an existing account. |
| `ONEAUTH_DATABASE_PATH` | SQLite path; `/data/oneauth.db` is correct for Compose. |

Target variables:

| Target | Variables |
| --- | --- |
| OPNsense | `ONEAUTH_OPNSENSE_ENABLED`, `ONEAUTH_OPNSENSE_BASE_URL`, `ONEAUTH_OPNSENSE_API_KEY`, `ONEAUTH_OPNSENSE_API_SECRET`, `ONEAUTH_OPNSENSE_VERIFY_TLS` |
| Nexus | `ONEAUTH_NEXUS_ENABLED`, `ONEAUTH_NEXUS_BASE_URL`, `ONEAUTH_NEXUS_ADMIN_USER`, `ONEAUTH_NEXUS_ADMIN_PASSWORD`, `ONEAUTH_NEXUS_DEFAULT_ROLES` (comma-separated) |
| Nextcloud | `ONEAUTH_NEXTCLOUD_ENABLED`, `ONEAUTH_NEXTCLOUD_BASE_URL`, `ONEAUTH_NEXTCLOUD_ADMIN_USER`, `ONEAUTH_NEXTCLOUD_ADMIN_PASSWORD` |

Keep target URLs free of trailing API paths; use the server base URL. Use valid
TLS certificates in production. `ONEAUTH_OPNSENSE_VERIFY_TLS=false` is intended
only for controlled testing.

## Target prerequisites

### OPNsense

Create a dedicated local API account and generate an API key and secret under
the account's API keys section. Give it permission to search, create, update,
and delete local users through the Auth User API. The connector authenticates
with the key and secret using HTTP Basic authentication. It sends `name`,
`descr`, `email`, `disabled`, and, when needed, `password` in the `user` payload.

### Nexus Repository

Use a dedicated local service account. It must be able to read, create, update,
and delete users and change local-user passwords. Nexus expresses the first
four as `nexus:users:read`, `nexus:users:create`, `nexus:users:update`, and
`nexus:users:delete`; current Nexus source protects the change-password endpoint
with `nexus:*`, so verify the least-privilege role against the exact Nexus
version in use. Every role listed in `ONEAUTH_NEXUS_DEFAULT_ROLES` must already
exist. The default `nx-anonymous` role is only an example and should be replaced
with the access intended for managed users.

### Nextcloud

The Provisioning API app must be enabled (it is enabled by default). Configure
a Nextcloud administrator account and preferably an app password rather than
the account's interactive password. The connector uses Basic authentication
and sends `OCS-APIRequest: true` on every request.

## Run and operate

This project must be managed through `compose-helper.sh`, which pins the Compose
project, compose file, and environment file.

```sh
# Validate the complete Compose model.
./compose-helper.sh --profile build config --quiet

# Build the local image and start the app detached.
./compose-helper.sh rebuild

# Check state and read bounded logs.
./compose-helper.sh ps
./compose-helper.sh logs --tail=100 oneauth
```

Open `http://localhost:8000`, sign in with the configured bootstrap admin, and
check **Targets** before creating users. A normal operator flow is:

1. Confirm each enabled target is reachable.
2. Create or edit a user on **Users**.
3. Review the target matrix on **Users** or **Targets**.
4. Inspect a failed cell's detail and use **Retry** after fixing the target.
5. Review **Audit** for the admin action and connector result.

Deleting a managed user deletes that account on every enabled target. If any
target fails, the local record remains so the operation can be retried.

To stop the service while preserving the database:

```sh
./compose-helper.sh stop
```

Do not use the helper's `down` command unless permanent deletion of the named
database volume is explicitly intended.

## Development and verification

Python 3.12 or newer is required.

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

Connector tests use recorded API shapes and mocked HTTP responses, so they do
not modify real targets. Before a production rollout, test create, password
change, disable, retry, and delete against non-production instances of the same
target versions.

Back up both the `oneauth-data` volume and `ONEAUTH_SECRET_KEY`. Losing or
changing the secret key makes any still-pending encrypted passwords impossible
to decrypt.
