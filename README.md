# One Auth (Non-SSO)

One Auth is a small administrative web app for maintaining local identities
across any number of configured OPNsense, Nexus Repository, Nextcloud, and SSH targets. It is
not an identity provider and does not provide single sign-on: it calls each
target's user-management API and records the result independently.

## What it does

- Provides a session-protected admin UI for creating, editing, disabling, and
  deleting managed users.
- Generates passwords or accepts operator-supplied passwords.
- Assigns any subset of configured targets to each non-root account; new accounts default to local-only.
- Propagates username, email, display name, password, and public key where each assigned target supports them.
- Shows per-user, per-target pending, successful, and failed states.
- Streams pending and retrying state changes into the Users and Targets tables without a page refresh.
- Persists capped exponential-backoff schedules across application restarts and also supports immediate manual retry.
- Soft-deletes users locally after remote deletion, with restore and explicit purge controls.
- Records admin actions and connector results in an audit log.

The protected root recovery account is local-only and cannot be assigned,
disabled, deleted, demoted, or expired into remote operations. Other local
accounts have `user` or `admin` roles. The SQLite database stores passwords as bcrypt hashes. A managed user's
plaintext password is never stored. While propagation is pending, the password
is encrypted with Fernet using a key derived from `ONEAUTH_SECRET_KEY`; the
encrypted value is removed after every enabled target succeeds.
SSH private keys are generated in the browser where supported or handled once
by the explicitly labelled compatibility flow; only public keys persist.

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
./compose-helper.sh demo-up
./compose-helper.sh demo-ps
```

Open `http://localhost:8000` and sign in with:

- Username: `admin`
- Password: `demo-password`

The **Targets** page should show all three services as reachable. Create,
edit, change the password of, disable, retry, and delete users through the
normal UI; One Auth uses its production connector implementations for every
operation.

The demo also includes two isolated Debian OpenSSH targets with runtime-generated
host keys: password-only and password-plus-key managed-user modes. Configure
each on **Targets** with SSH admin `provisioner` and password
`demo-ssh-admin`, then select **SAVE** to store and test them. The combined target exercises the
browser-first or labelled one-time key flow; no reusable host private key is
stored in the repository.

Open `http://localhost:9000` to control mock availability. OPNsense, Nexus,
and Nextcloud each have one independent success/failure switch; the selected
mode applies consistently to every API request for that whole target. This
makes failed states and automatic recovery deterministic. The control page is
bound to host loopback only, and health checks remain successful regardless of
target switches.

The demo is isolated from the normal runtime:

- `oneauth-demo` has explicit demo-only settings and a separate
  `oneauth-demo-data` volume. It never reads target credentials from
  `.config/.env`.
- Connector-facing mock APIs remain inside the Compose network. The browser
  controls are published only on host loopback port 9000.
- Mock target users live in memory and reset whenever `mock-targets` is
  restarted. The One Auth demo database persists, so delete evaluated users
  in the UI before restarting the mocks when you want both sides to remain in
  sync.
- The fixed credentials and secret key are public demo values. Never copy
  them into a real deployment.

To stop the demo without deleting its database:

```sh
./compose-helper.sh demo-stop
```

To remove the demo containers and reset its database:

```sh
./compose-helper.sh demo-down
```

The complete demo command family is:

| Command | Effect |
| --- | --- |
| `demo-up` / `demo-start` | Start the demo detached without building. |
| `demo-rebuild` | Rebuild the local image and recreate the demo. |
| `demo-build` | Rebuild the local image without starting the demo. |
| `demo-pull` | Pull images used by the demo services. |
| `demo-restart` | Stop and start the demo without rebuilding. |
| `demo-stop` | Stop the demo while preserving its database. |
| `demo-down` | Remove demo containers and reset its database. |
| `demo-logs` | Follow logs for the demo application and mocks. |
| `demo-ps` | Show status for the demo application and mocks. |

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
| `ONEAUTH_CONFIG_FILE` | YAML registry path; Compose mounts it read-only at `/config/oneauth.yaml`. |
| `ONEAUTH_RETRY_SCAN_SECONDS` | Frequency of the single-process recovery scan; default `5`. |
| `ONEAUTH_RETRY_BASE_SECONDS` | First automatic retry delay; default `5`. |
| `ONEAUTH_RETRY_MAX_SECONDS` | Cap for exponential retry delay; default `300`. |

Copy `oneauth.example.yaml`, give every instance a stable unique ID, and set
`ONEAUTH_CONFIG_FILE` to the copy. The ordered `targets` list accepts repeated
types and contains non-secret endpoint metadata. In **Targets**, enter each
instance's management credentials and select **SAVE**. Saving encrypts the
credentials and immediately probes that revision. Credentials are
encrypted in SQLite with `ONEAUTH_SECRET_KEY`, never rendered back, and a
replacement remains unverified if authentication or connectivity fails. Until
the current credential revision passes its probe, the target cannot be
assigned, synchronized, or retried. The Targets page reports one combined
status, including fully configured, authentication failed, and Unreachable;
the safe probe detail remains available as a status tooltip.

OPNsense accepts its API key and secret; Nexus and Nextcloud accept their admin
username and password. SSH accepts an SSH admin username plus either a password
or uploaded private key. SSH YAML still pins host fingerprint, platform,
username policy, and managed-user password/key mode. Password acceptance rules,
three-password history by default, expiry acknowledgement, and browser/server
key policy are configured in the same file.

Target entries can also declare the memberships every managed user receives:
`default_groups` for OPNsense, Nextcloud, and SSH, and `default_roles` for
Nexus. These values are non-secret YAML policy. The named groups or roles must
already exist on the target; a missing membership makes synchronization fail
instead of silently provisioning a less-privileged account. SSH appends its
configured supplementary groups and preserves any other memberships.

Existing type-keyed sync rows migrate only when exactly one configured target
has that type. Zero or multiple matches remain retired for operator resolution;
removed target IDs also retain history. Unassigning a target disables rather
than deletes its remote account. Reassignment re-enables a known account, or
shows `awaiting_credentials` until a verified login, password change, or admin
reset supplies a short-lived credential. That state is not automatically retried.

Keep target URLs free of trailing API paths; use the server base URL. Use valid
TLS certificates in production. `ONEAUTH_OPNSENSE_VERIFY_TLS=false` is intended
only for controlled testing.

## Target prerequisites

### OPNsense

Create a dedicated local API account and generate an API key and secret under
the account's API keys section. Give it permission to search, create, update,
and delete local users through the Auth User API. The connector authenticates
with the key and secret using HTTP Basic authentication. It sends `name`,
`descr`, `email`, `disabled`, configured `default_groups` as
`group_memberships`, and, when needed, `password` in the `user` payload. Use the
group identifiers expected by the installed Auth User API version.

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
and sends `OCS-APIRequest: true` on every request. Every `default_groups` name
must already exist; One Auth supplies the groups at creation and reconciles any
missing configured membership for existing users.

### SSH

Create each `default_groups` supplementary group before probing and assigning
the target. The SSH admin needs access to `getent group` and passwordless sudo
permission for `usermod -aG` in addition to the documented user-management
commands.

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
assigned target. Failures retry automatically with capped exponential backoff;
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
to decrypt and also makes encrypted target management credentials unavailable.
