# NA-SSO demo

The standalone demo runs the real NA-SSO application and production
connectors against stateful, protocol-faithful mock OPNsense, Nexus Repository,
and Nextcloud APIs plus two isolated Debian OpenSSH targets. It does not contact
or modify real target systems.

## Start the demo

Docker with Compose v2 is required. Build both local images and start the
isolated demo project:

```sh
./compose-helper.sh demo-rebuild
./compose-helper.sh demo-ps
```

Open <http://localhost:8001> and sign in with:

- Username: `admin`
- Password: `demo-password`

The demo uses `docker-compose-demo.yaml`, the `<normal-project>-demo` Compose
project, and public bootstrap settings from `.config-demo/.env`. It does not
read `.config/` and can run alongside the normal application on port 8000.

## Configure the demo targets

Target management credentials are intentionally not preloaded. Enter them on
**Targets** and select **SAVE**; NA-SSO encrypts each value and immediately
probes that target revision.

Expand one target row at a time and enter the following values.

### Firewall A and Firewall B

Configure both OPNsense mock targets identically:

| UI field | Value |
| --- | --- |
| **API key** | `demo-key` |
| **API secret** | `demo-secret` |

Select **SAVE** on each firewall separately.

### Nexus Repository

| UI field | Value |
| --- | --- |
| **Admin user** | `admin` |
| **Admin password** | `demo-password` |

### Nextcloud

| UI field | Value |
| --- | --- |
| **Admin user** | `admin` |
| **Admin password** | `demo-password` |

### Debian SSH password

This target provisions managed users in password-only mode. Configure its SSH
management login with the demo password:

| UI field | Value |
| --- | --- |
| **SSH admin** | `provisioner` |
| **Authentication** | `Password` |
| **Admin password** | `demo-ssh-admin` |
| **Private key** | Leave empty |

### Debian SSH key and password

This target provisions both managed-user passwords and authorized public keys.
Use the generated SSH management private key to demonstrate key-based target
administration:

| UI field | Value |
| --- | --- |
| **SSH admin** | `provisioner` |
| **Authentication** | `Private key` |
| **Admin password** | Leave empty |
| **Private key** | Upload `.config-demo/management_key` |

The authentication selector shows only the credentials required by the chosen
mode. **Password** shows the password field, **Private key** shows the upload,
and **Password + private key** shows and requires both. The combined option is
for SSH servers configured to require both factors for the management login.

Upload `management_key` itself—not `management_key.pub`,
`password_host_key`, or `combined_host_key`. The public half is already
installed for `provisioner` inside both demo SSH targets. The host-key files
identify the servers and are not login credentials.

All values and generated keys in this guide are public, disposable demo
credentials. Never reuse them outside this stack.

After each save, the target should report **fully configured**. Expand a target
to see credential verification separately from current reachability, its
revision and UTC check history, retry state, and **Test connection**. A failed
save remains expanded with sanitized inline guidance. If the generated key is
not present yet, confirm `demo-rebuild` or `demo-up` completed and that
`.config-demo/management_key` exists.

## Complete a managed user's first login

Passwords set when an administrator creates, resets, or restores an account are
temporary local credentials. NA-SSO deliberately does not send them to target
systems. Assigned targets show **CHPW** and their accounts remain uncreated or
disabled until the user signs in to NA-SSO and chooses a replacement password.

To complete the transition:

1. Create the user and assign the desired targets. For a generated password,
   reveal or copy the full value and confirm that it was saved before submitting;
   closing the modal without confirmation discards it. Otherwise retain the
   manually entered value.
2. Sign out as the administrator and sign in as the managed user with that
   temporary password.
3. Choose a replacement password on the required password-change page.
4. Return to the account page. The replacement is propagated to assigned
   password-capable targets, and the account page shows its expiry date.

The administrator's **Users** table shows the same expiry date. With the demo's
90-day policy, it is calculated from the user-chosen replacement, not from the
temporary password.

The demo uses a one-time 14-day grace acknowledgement for an expired password.
The user sees the resulting date and acknowledgement count before selecting
**Keep until …**. The original password-change date remains unchanged; after
the grace period, that same password must be replaced.

The SSH target mode and its management authentication are separate concepts:
the selected **Authentication** field controls how NA-SSO logs in as
`provisioner`; the target's generated YAML `mode` controls whether managed
users receive a password, a public key, or both.

### Exercise managed-user SSH keys

The uploaded `management_key` authenticates NA-SSO as the target
administrator; it is not a managed user's login key. To exercise the combined
target's managed-user key propagation:

1. Create a non-root user, give it a password, and assign **Debian SSH key and
   password**.
2. Sign out as the administrator and sign in as that managed user.
3. Complete the first-login password decision, then open **My account** from the
   account menu.
4. Select **Generate in browser**, inspect the new fingerprint, then save the
   `na-sso_ed25519` file or copy the full private-key value.
5. Confirm that the private key is safe, then select **Enrol public key**. Only
   its public half is sent to NA-SSO and propagated to the combined SSH target;
   the previously enrolled key remains active until this succeeds.
6. Alternatively, use the clearly labelled compatibility fallback; it displays
   a server-generated private key once and does not persist it.

To exercise zero-downtime rotation, add a second named key and select **Replace
an existing key**. NA-SSO installs the replacement before revoking the old key,
then retains the revoked fingerprint in account history. **Emergency revoke**
requires the managed user's current password and removes the selected key from
every assigned SSH target. Connector metadata reports managed-key last use as
unsupported until a target can provide trustworthy evidence; NA-SSO does not
invent a timestamp.

The password-only SSH target receives the managed-user password but ignores
the enrolled public key. The combined target receives both.

The managed user's **My access** section lists assigned targets, plain-language
propagation state, supported sign-in method, and scheduled retry time. Failures
show the demo's configured operator-help guidance without exposing target
management credentials or raw connector detail.

## Exercise automation and service accounts

Only the protected Root account can create service accounts. Open **Service
accounts**, choose the smallest capabilities needed, and issue a short-lived
credential. The `nas_...` token is displayed once; copy it before leaving the
page. Rotation can overlap an old and new credential, and either a credential
or the whole account can be revoked independently.

The versioned API advertises the resources available to that credential:

```sh
curl -H 'Authorization: Bearer nas_REPLACE_WITH_DEMO_TOKEN' \
  http://localhost:8001/api/v1
```

Use `na-ssoctl --base-url http://localhost:8001 --token-file <path> ...` for
bounded user and target listings, bulk preview/apply, reconciliation
preview/apply, operation status, and audit export. Prefer `--token-file` to
placing a credential directly in shell history. API responses include a request
ID and a consistent envelope; supported mutations require an idempotency key.
See [CONNECTORS.md](CONNECTORS.md) for the connector capability document and
dry-run/inspection contract exposed by target responses.

## Review unmanaged target accounts

Open **Unmanaged accounts** as Root and start a read-only discovery scan. The
SSH demo targets will normally report `provisioner`; discovery itself never
changes a remote system. You can:

- Ignore an expected account and retain that decision across later scans.
- Adopt an account into NA-SSO without mutating it remotely; the managed record
  begins in the normal password-change-required state.
- Review protected exclusions configured for service and break-glass users.

Remote removal is disabled in the demo policy by default. When an operator
explicitly enables it in a disposable environment, removal still requires
Root, a short-lived one-use approval token, exact account confirmation, and a
recovery acknowledgement.

## What is generated

On first start, `demo-ssh-bootstrap` creates the following ignored files under
`.config-demo/`:

- `na-sso.yaml` containing the effective six-target registry
- `management_key` and `management_key.pub` for the SSH management login
- Password-target host key and public key
- Combined-target host key and public key

The effective YAML contains fingerprints derived from those generated host
keys. `management_key` is deliberately host-readable so it can be selected in
the browser's upload control; it is a public, disposable demo credential. No
reusable production private key is committed to the repository.

## Exercise failures and recovery

Open <http://localhost:9000> to control mock API availability. OPNsense, Nexus,
and Nextcloud each have an independent whole-target success/failure switch.
Health checks remain healthy while a target is switched to failure, allowing
deterministic synchronization failures and automatic recovery testing.

Useful evaluation flows include:

1. Create a user, assign selected targets, and observe **CHPW** before first
   login.
2. Sign in as that user, replace the temporary password, and watch pending
   states update on **Users**.
3. Disable and re-enable an account.
4. Force one mock target offline, observe retry state, then restore it.
5. Delete and restore with a temporary password, complete **CHPW** again, and
   explicitly purge a completed record.
6. Review administrative and connector events under **Audit**.
7. Exercise search, lifecycle/target/issue filters, sorting, the dedicated
   account detail page, and the mobile card inventory.
8. Select accounts for bulk onboarding assignment, offboarding unassignment,
   disable, or retry; inspect the no-change preview, then confirm and note the
   partial-outcome correlation ID.
9. Upload a CSV with onboard/offboard rows, inspect validation, execute valid
   rows, download credential-free results, and consume new temporary passwords
   once.
10. Open **Reconciliation**, preview a user/target, change a remote mock record
    or assignment, and approve a correlated repair.
11. Publish an assignment-profile version, preview it against a user, preserve
    a direct target as an exception, and add a visible membership override.
12. Record owner/reason and a temporary lifecycle window, then create and open
    an access review, send a reminder, and attest a retain or disable decision.
13. Create a scoped service account, call `/api/v1`, rotate its one-time token,
    and verify revoked credentials fail without leaking secret material.
14. Run unmanaged-account discovery, ignore the SSH `provisioner` account, and
    confirm a second scan remains read-only and preserves that decision.
15. Enrol two named SSH keys and replace the first with the second; confirm the
    account history shows the old fingerprint revoked and the SSH target holds
    exactly one active key.

Mock API user state is in memory and resets whenever `mock-targets` restarts.
The NA-SSO demo database persists independently, so delete evaluated users
before restarting mocks when both sides need to stay aligned.

## Demo lifecycle commands

| Command | Effect |
| --- | --- |
| `demo-up` / `demo-start` | Start the existing demo images without building. |
| `demo-rebuild` | Rebuild both local images and recreate the demo. |
| `demo-build` | Rebuild demo images without starting services. |
| `demo-restart` | Stop and start the demo without rebuilding. |
| `demo-stop` | Stop the demo while preserving its database and generated files. |
| `demo-down` | Remove demo containers, volumes, generated YAML, and SSH keys. |
| `demo-logs` | Follow application, mock API, and SSH logs. |
| `demo-ps` | Show demo service status. |
| `demo-compose …` | Pass advanced arguments to the isolated demo Compose project. |

For bounded logs that return instead of following indefinitely:

```sh
./compose-helper.sh demo-compose logs --tail=100
```

`demo-down` is a complete reset. The next start generates new SSH host keys and
matching fingerprints.

## Troubleshooting

- **Authentication failed:** verify the exact credentials in the table above;
  Nexus and Nextcloud use `demo-password`, not `admin123`.
- **SSH private-key upload fails:** choose `.config-demo/management_key`, select
  **Private key**, leave **Admin password** empty, and do not upload a `.pub` or
  host-key file.
- **Image not found:** run `./compose-helper.sh demo-build` before `demo-up`.
- **Port 8001 is busy:** stop the process using it or change the demo port
  mapping in `docker-compose-demo.yaml`.
- **Target state differs from mock state:** reset with `demo-down`, then start a
  clean demo.
- **Inspect the effective model:** run
  `./compose-helper.sh demo-compose --profile build config`.
