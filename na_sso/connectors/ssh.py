from __future__ import annotations

import shlex
from pathlib import Path

import asyncssh

from na_sso.config import SshTarget
from na_sso.connectors.base import AccountDiscovery, Connector, DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_OPERATION_TIMEOUT_SECONDS, IdentityCapabilities, IdentityValidation, RemoteAccount, SyncResult
from na_sso.models import ManagedUser
from na_sso.reconciliation import (
    InspectionCapabilities,
    ReconciliationReport,
    RemoteIdentitySnapshot,
    compare_snapshot,
    unavailable_report,
)
from na_sso.security import ssh_public_key_fingerprint


class _PinnedClient(asyncssh.SSHClient):
    def __init__(self, fingerprint: str):
        self.fingerprint = fingerprint

    def validate_host_public_key(self, host, addr, port, key):
        return key.get_fingerprint("sha256") == self.fingerprint


class SSHConnector(Connector):
    """Pinned-host SSH provisioning with password, key, or combined authentication."""

    capabilities = IdentityCapabilities(display_name=True, password=True, public_key=True)

    def __init__(self, target: SshTarget):
        self.target_id, self.target_type, self.display_name = target.id, target.type, target.display_name
        self._target = target
        self.inspection_capabilities = InspectionCapabilities(
            display_name=True,
            status=True,
            memberships=True,
            public_key=target.mode in {"key", "password_and_key"},
            memberships_exact=False,
        )

    @property
    def default_memberships(self) -> frozenset[str]:
        return frozenset(self._target.default_groups)

    def validate_identity(self, user: ManagedUser) -> IdentityValidation:
        if any(ch in user.username for ch in ":\n\r/\0") or not user.username or len(user.username) > 32:
            return IdentityValidation(False, f"{self.display_name} cannot safely represent the username")
        if not self._target.allow_relaxed_usernames and not user.username.replace("-", "_").isidentifier():
            return IdentityValidation(False, f"{self.display_name} requires a portable Unix username")
        return IdentityValidation(True)

    async def _connect(self):
        kwargs = {}
        if self._target.management_password:
            kwargs["password"] = self._target.management_password.get_secret_value()
        if self._target.management_private_key or self._target.management_private_key_file:
            material = (self._target.management_private_key.get_secret_value()
                        if self._target.management_private_key
                        else Path(self._target.management_private_key_file).read_text(encoding="utf-8"))
            kwargs["client_keys"] = [asyncssh.import_private_key(material)]
        return await asyncssh.connect(
            self._target.host, port=self._target.port, username=self._target.management_user,
            known_hosts=None, **kwargs,
            client_factory=lambda: _PinnedClient(self._target.host_key_sha256),
            connect_timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )

    async def _run(self, conn, command: str, *, input_data: str | None = None):
        result = await conn.run(command, input=input_data, check=False, timeout=DEFAULT_OPERATION_TIMEOUT_SECONDS)
        if result.exit_status != 0:
            raise RuntimeError(result.stderr.strip() or f"command exited {result.exit_status}")
        return result.stdout

    async def _exists(self, conn, username: str) -> bool:
        result = await conn.run(f"getent passwd {shlex.quote(username)}", check=False, timeout=10)
        return result.exit_status == 0

    async def _create(self, conn, user: ManagedUser) -> None:
        qname, qdisplay = shlex.quote(user.username), shlex.quote(user.display_name or user.username)
        relaxed = (self._target.allow_relaxed_usernames and
                   not user.username.replace("-", "_").isidentifier())
        if self._target.platform in {"debian", "ubuntu"}:
            flag = ""
            if relaxed:
                help_text = await self._run(conn, "adduser --help 2>&1")
                if "--allow-bad-names" in help_text:
                    flag = "--allow-bad-names "
                elif "--force-badname" in help_text:
                    flag = "--force-badname "
                else:
                    raise RuntimeError("installed adduser cannot create relaxed usernames")
            await self._run(conn, f"sudo -n adduser --disabled-password --gecos {qdisplay} {flag}{qname}")
        else:
            flag = ""
            if relaxed:
                help_text = await self._run(conn, "useradd --help 2>&1")
                if "--badname" not in help_text:
                    raise RuntimeError("installed useradd cannot create relaxed usernames")
                flag = "--badname "
            await self._run(conn, f"sudo -n useradd -m -c {qdisplay} {flag}{qname}")

    async def inspect_user(self, user: ManagedUser) -> ReconciliationReport:
        return await self.inspect_user_for_assignment(
            user, frozenset(self._target.default_groups)
        )

    async def discover_accounts(self) -> AccountDiscovery:
        try:
            async with await self._connect() as conn:
                result = await self._run(conn, "getent passwd")
            accounts = []
            for line in result.splitlines():
                parts = line.split(":")
                if len(parts) < 7:
                    continue
                try:
                    uid = int(parts[2])
                except ValueError:
                    continue
                accounts.append(RemoteAccount(
                    username=parts[0], display_name=parts[4].split(",", 1)[0], uid=uid,
                ))
            return AccountDiscovery(True, tuple(accounts))
        except (asyncssh.Error, OSError, RuntimeError, TimeoutError):
            return AccountDiscovery(True, detail="SSH account discovery failed.")

    async def inspect_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> ReconciliationReport:
        validation = self.validate_identity(user)
        if not validation.ok:
            return unavailable_report(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                detail=validation.detail,
                required_memberships=memberships,
            )
        try:
            async with await self._connect() as conn:
                qname = shlex.quote(user.username)
                identity = await conn.run(f"getent passwd {qname}", check=False, timeout=10)
                if identity.exit_status == 2:
                    snapshot = RemoteIdentitySnapshot(present=False)
                elif identity.exit_status != 0:
                    return unavailable_report(
                        target_id=self.target_id,
                        target_name=self.display_name,
                        user=user,
                        capabilities=self.inspection_capabilities,
                        detail="SSH identity read failed.",
                        required_memberships=memberships,
                    )
                else:
                    parts = identity.stdout.rstrip("\n").split(":")
                    display_name = parts[4].split(",", 1)[0] if len(parts) > 4 else None

                    groups_result = await conn.run(f"id -nG {qname}", check=False, timeout=10)
                    actual_memberships = (
                        frozenset(groups_result.stdout.split())
                        if groups_result.exit_status == 0
                        else None
                    )

                    status_result = await conn.run(f"sudo -n passwd -S {qname}", check=False, timeout=10)
                    status_parts = status_result.stdout.split()
                    status = None
                    if status_result.exit_status == 0 and len(status_parts) >= 2:
                        status = "disabled" if status_parts[1].upper().startswith("L") else "active"

                    fingerprints = None
                    if self.inspection_capabilities.public_key:
                        key_path = shlex.quote(f"/home/{user.username}/.ssh/authorized_keys")
                        exists = await conn.run(f"sudo -n test -f {key_path}", check=False, timeout=10)
                        if exists.exit_status == 1:
                            fingerprints = frozenset()
                        elif exists.exit_status == 0:
                            key_result = await conn.run(f"sudo -n cat {key_path}", check=False, timeout=10)
                            if key_result.exit_status == 0:
                                parsed = [
                                    ssh_public_key_fingerprint(line.strip())
                                    for line in key_result.stdout.splitlines()
                                    if line.strip() and not line.lstrip().startswith("#")
                                ]
                                if all(parsed):
                                    fingerprints = frozenset(item for item in parsed if item)

                    snapshot = RemoteIdentitySnapshot(
                        present=True,
                        username=parts[0] if parts else None,
                        display_name=display_name,
                        status=status,
                        memberships=actual_memberships,
                        public_key_fingerprints=fingerprints,
                    )
            return compare_snapshot(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                snapshot=snapshot,
                required_memberships=memberships,
            )
        except (asyncssh.Error, OSError, RuntimeError, TimeoutError):
            return unavailable_report(
                target_id=self.target_id,
                target_name=self.display_name,
                user=user,
                capabilities=self.inspection_capabilities,
                detail="SSH identity read failed.",
                required_memberships=memberships,
            )

    async def ensure_user(self, user: ManagedUser, password: str | None) -> SyncResult:
        return await self.ensure_user_for_assignment(
            user, password, frozenset(self._target.default_groups)
        )

    async def ensure_user_for_assignment(
        self, user: ManagedUser, password: str | None, memberships: frozenset[str]
    ) -> SyncResult:
        validation = self.validate_identity(user)
        if not validation.ok:
            return SyncResult(False, validation.detail)
        try:
            async with await self._connect() as conn:
                if not await self._exists(conn, user.username):
                    if password is None and self._target.mode == "password":
                        return SyncResult(False, "SSH password mode requires a credential for new users")
                    await self._create(conn, user)
                qname = shlex.quote(user.username)
                if password is not None and self._target.mode in {"password", "password_and_key"}:
                    # chpasswd consumes stdin; the password never appears in argv or a command string.
                    await self._run(conn, "sudo -n chpasswd", input_data=f"{user.username}:{password}\n")
                if self._target.mode in {"key", "password_and_key"}:
                    path = f"/home/{user.username}/.ssh"
                    qpath = shlex.quote(path)
                    await self._run(conn, f"sudo -n install -d -m 700 -o {qname} -g {qname} {qpath}")
                    keys = user.active_ssh_public_keys
                    payload = "\n".join(keys) + ("\n" if keys else "")
                    await self._run(conn, f"sudo -n tee {qpath}/authorized_keys >/dev/null", input_data=payload)
                    await self._run(conn, f"sudo -n chown {qname}:{qname} {qpath}/authorized_keys && sudo -n chmod 600 {qpath}/authorized_keys")
                if memberships:
                    groups = ",".join(sorted(memberships))
                    for group in sorted(memberships):
                        await self._run(conn, f"getent group {shlex.quote(group)}")
                    await self._run(conn, f"sudo -n usermod -aG {shlex.quote(groups)} {qname}")
                action = "-L" if user.status == "disabled" else "-U"
                await self._run(conn, f"sudo -n usermod {action} {qname}")
            return SyncResult(True, "saved")
        except (asyncssh.Error, OSError, RuntimeError, TimeoutError) as error:
            return SyncResult(False, f"ssh error: {error}")

    async def disable_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with await self._connect() as conn:
                if await self._exists(conn, user.username):
                    await self._run(conn, f"sudo -n usermod -L {shlex.quote(user.username)}")
            return SyncResult(True, "disabled")
        except (asyncssh.Error, OSError, RuntimeError, TimeoutError) as error:
            return SyncResult(False, f"ssh error: {error}")

    async def disable_user_for_assignment(
        self, user: ManagedUser, memberships: frozenset[str]
    ) -> SyncResult:
        return await self.ensure_user_for_assignment(user, None, memberships)

    async def delete_user(self, user: ManagedUser) -> SyncResult:
        try:
            async with await self._connect() as conn:
                if await self._exists(conn, user.username):
                    await self._run(conn, f"sudo -n userdel -r {shlex.quote(user.username)}")
            return SyncResult(True, "deleted")
        except (asyncssh.Error, OSError, RuntimeError, TimeoutError) as error:
            return SyncResult(False, f"ssh error: {error}")

    async def probe(self) -> SyncResult:
        try:
            async with await self._connect() as conn:
                await self._run(conn, "true")
            return SyncResult(True, "reachable")
        except (asyncssh.Error, OSError, RuntimeError, TimeoutError) as error:
            return SyncResult(False, f"ssh unreachable: {error}")
