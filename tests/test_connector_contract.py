from na_sso.connectors.base import (
    CONNECTOR_CONTRACT_VERSION,
    Connector,
    ConnectorErrorKind,
    SyncResult,
)
from na_sso.models import ManagedUser
from na_sso.reconciliation import (
    DriftState,
    FieldComparison,
    ReconciliationField,
    ReconciliationReport,
)


class PlanningConnector(Connector):
    target_id = "planning"
    target_type = "test"
    display_name = "Planning target"

    def __init__(self):
        self.mutations = 0
        self.reads = 0

    async def inspect_user(self, user):
        self.reads += 1
        return ReconciliationReport(
            self.target_id, self.display_name, user.username,
            (
                FieldComparison(ReconciliationField.DISPLAY_NAME, DriftState.DRIFT, "New", "Old"),
                FieldComparison(ReconciliationField.STATUS, DriftState.UNKNOWN, "active", detail="hidden"),
            ),
        )

    async def ensure_user(self, user, password):
        self.mutations += 1
        return SyncResult(True)

    async def disable_user(self, user):
        self.mutations += 1
        return SyncResult(True)

    async def delete_user(self, user):
        self.mutations += 1
        return SyncResult(True)

    async def probe(self):
        return SyncResult(True)


async def test_dry_run_uses_inspection_only_and_returns_bounded_actions():
    connector = PlanningConnector()

    plan = await connector.plan_user(ManagedUser(username="jdoe"))

    assert plan.supported
    assert plan.actions == ("set display_name",)
    assert plan.blockers == ("cannot observe status",)
    assert connector.reads == 1 and connector.mutations == 0


def test_connector_contract_is_versioned_and_machine_readable():
    connector = PlanningConnector()
    contract = connector.contract_metadata()
    assert contract.version == CONNECTOR_CONTRACT_VERSION == "1.0"
    assert contract.inspect and contract.dry_run
    assert not contract.account_discovery
    assert contract.connect_timeout_seconds > 0
    assert contract.operation_timeout_seconds >= contract.connect_timeout_seconds
    assert set(contract.error_kinds) == {item.value for item in ConnectorErrorKind}


def test_failed_results_always_receive_error_taxonomy_and_retry_semantics():
    unavailable = SyncResult(False, "ssh error: connection refused")
    invalid = SyncResult(False, "target requires email")
    timeout = SyncResult(False, "operation timed out")
    assert (unavailable.error_kind, unavailable.retryable) == (ConnectorErrorKind.UNAVAILABLE, True)
    assert (invalid.error_kind, invalid.retryable) == (ConnectorErrorKind.VALIDATION, False)
    assert (timeout.error_kind, timeout.retryable) == (ConnectorErrorKind.TIMEOUT, True)


def test_every_builtin_connector_declares_read_discovery_and_dry_run_capabilities():
    from na_sso.config import GiteaTarget, GitlabTarget, ImmichTarget, JenkinsTarget, NextcloudTarget, NexusTarget, OpnsenseTarget, SshTarget
    from na_sso.connectors.gitea import GiteaConnector
    from na_sso.connectors.gitlab import GitlabConnector
    from na_sso.connectors.immich import ImmichConnector
    from na_sso.connectors.jenkins import JenkinsConnector
    from na_sso.connectors.nextcloud import NextcloudConnector
    from na_sso.connectors.nexus import NexusConnector
    from na_sso.connectors.opnsense import OPNsenseConnector
    from na_sso.connectors.ssh import SSHConnector
    connectors = [
        NextcloudConnector(NextcloudTarget(
            id="cloud", type="nextcloud", display_name="Cloud", base_url="https://cloud",
            admin_user="admin", admin_password="secret",
        )),
        NexusConnector(NexusTarget(
            id="nexus", type="nexus", display_name="Nexus", base_url="https://nexus",
            admin_user="admin", admin_password="secret",
        )),
        OPNsenseConnector(OpnsenseTarget(
            id="firewall", type="opnsense", display_name="Firewall", base_url="https://fw",
            api_key="key", api_secret="secret",
        )),
        SSHConnector(SshTarget(
            id="shell", type="ssh", display_name="Shell", host="shell",
            management_user="manager", management_password="secret",
            host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="ubuntu",
        )),
        GitlabConnector(GitlabTarget(
            id="gitlab", type="gitlab", display_name="GitLab", base_url="https://gitlab", api_token="secret",
        )),
        GiteaConnector(GiteaTarget(
            id="gitea", type="gitea", display_name="Gitea", base_url="https://gitea", api_token="secret",
        )),
        ImmichConnector(ImmichTarget(
            id="immich", type="immich", display_name="Immich", base_url="https://immich", api_token="secret",
        )),
        JenkinsConnector(JenkinsTarget(
            id="jenkins", type="jenkins", display_name="Jenkins", base_url="https://jenkins",
            admin_user="admin", api_token="secret",
        )),
    ]
    for connector in connectors:
        contract = connector.contract_metadata()
        assert contract.version == "1.0"
        assert contract.inspect and contract.account_discovery and contract.dry_run
        assert not contract.public_key_last_used
