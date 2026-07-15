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

Upload `management_key` itself—not `management_key.pub`,
`password_host_key`, or `combined_host_key`. The public half is already
installed for `provisioner` inside both demo SSH targets. The host-key files
identify the servers and are not login credentials.

All values and generated keys in this guide are public, disposable demo
credentials. Never reuse them outside this stack.

After each save, the target should report **fully configured**. If the generated
key is not present yet, confirm `demo-rebuild` or `demo-up` completed and that
`.config-demo/management_key` exists.

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
3. Complete the first-login password decision, then open **Account**.
4. Select **Generate key in this browser** and save the downloaded
   `na-sso_ed25519` private key. Only its public half is sent to NA-SSO and
   propagated to the combined SSH target.
5. Alternatively, use the clearly labelled compatibility fallback; it displays
   a server-generated private key once and does not persist it.

The password-only SSH target receives the managed-user password but ignores
the enrolled public key. The combined target receives both.

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

1. Create a local-only user, then assign selected targets.
2. Change a password and watch pending states update on **Users**.
3. Disable and re-enable an account.
4. Force one mock target offline, observe retry state, then restore it.
5. Delete, restore with a new password, and explicitly purge a completed record.
6. Review administrative and connector events under **Audit**.

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
