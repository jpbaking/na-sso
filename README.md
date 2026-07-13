# One Auth (Non-SSO)

One Auth is a small administrative web app for maintaining the same local
username and password across OPNsense, Nexus Repository, and Nextcloud. It is
not an identity provider and does not provide single sign-on: it calls each
target's user-management API and records the result independently.

## What it does

- Provides a session-protected admin UI for creating, editing, disabling, and
  deleting managed users.
- Generates passwords or accepts operator-supplied passwords.
- Propagates user changes to every enabled target and automatically retries failures.
- Shows per-user, per-target pending, successful, and failed states.
- Persists capped exponential-backoff schedules across application restarts and also supports immediate manual retry.
- Soft-deletes users locally after remote deletion, with restore and explicit purge controls.
- Records admin actions and connector results in an audit log.

The SQLite database stores admin passwords as bcrypt hashes. A managed user's
plaintext password is never stored. While propagation is pending, the password
is encrypted with Fernet using a key derived from `ONEAUTH_SECRET_KEY`; the
encrypted value is removed after every enabled target succeeds.

## Try the complete UI with mock targets

The optional `demo` Compose profile runs the real One Auth application against
stateful, protocol-faithful mock OPNsense, Nexus Repository, and Nextcloud
APIs. It is intended for evaluation and UI/UX exploration only; it does not
run or modify any real target system.

If this is a fresh checkout, create the normal local environment file first.
The demo service supplies its own target settings, so no target URLs or
credentials need to be edited:

```sh
test -f .config/.env || cp .config/.env.example .config/.env
./compose-helper.sh build
./compose-helper.sh stop oneauth
./compose-helper.sh --profile demo up -d oneauth-demo
./compose-helper.sh --profile demo ps
```

Open `http://localhost:8000` and sign in with:

- Username: `admin`
- Password: `demo-password`

The **Targets** page should show all three services as reachable. Create,
edit, change the password of, disable, retry, and delete users through the
normal UI; One Auth uses its production connector implementations for every
operation.

The demo is isolated from the normal runtime:

- `oneauth-demo` has explicit demo-only settings and a separate
  `oneauth-demo-data` volume. It never reads target credentials from
  `.config/.env`.
- `mock-targets` is reachable only inside the Compose network. Its reset and
  failure-injection controls are intentionally not published to the host.
- Mock target users live in memory and reset whenever `mock-targets` is
  restarted. The One Auth demo database persists, so delete evaluated users
  in the UI before restarting the mocks when you want both sides to remain in
  sync.
- The fixed credentials and secret key are public demo values. Never copy
  them into a real deployment.

To stop the demo without deleting its database:

```sh
./compose-helper.sh --profile demo stop oneauth-demo mock-targets
```

To return to a configured real-integration runtime, stop the demo first and
then run `./compose-helper.sh start`.

## Architecture

The FastAPI application serves Jinja templates and a bundled lazyway.io design
kit. SQLAlchemy stores managed users, target sync states, and audit events in a
SQLite database on the `oneauth-data` volume. Connector implementations use
`httpx` to call:

- OPNsense: `/api/auth/user/*`
- Nexus Repository: `/service/rest/v1/security/users/*`
- Nextcloud: `/ocs/v1.php/cloud/users/*`

Initial operations run after the HTTP response as FastAPI background tasks. A
single in-process recovery worker scans persisted retry schedules and replays
due target operations. This is suited to one application process; use a
distributed lock and durable external queue before scaling to multiple workers.

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
| `ONEAUTH_RETRY_SCAN_SECONDS` | Frequency of the single-process recovery scan; default `5`. |
| `ONEAUTH_RETRY_BASE_SECONDS` | First automatic retry delay; default `5`. |
| `ONEAUTH_RETRY_MAX_SECONDS` | Cap for exponential retry delay; default `300`. |

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

Deleting a managed user marks it for deletion and deletes the account on every
enabled target. Failures retry automatically with capped exponential backoff;
the UI also offers immediate target retry. The local record remains after all
targets succeed as a soft-deleted audit record. Restore requires a new password
and reprovisions all enabled targets. Permanent local removal requires the
explicit **Purge** action, available only after remote deletion completes.

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
not modify real targets. The mock-target integration tests also open only a
temporary loopback server. Before a production rollout, test create, password
change, disable, retry, and delete against non-production instances of the same
target versions.

Back up both the `oneauth-data` volume and `ONEAUTH_SECRET_KEY`. Losing or
changing the secret key makes any still-pending encrypted passwords impossible
to decrypt.
