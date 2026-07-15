from pathlib import Path

import pytest
from pydantic import ValidationError

from na_sso.config import FileConfig, SshTarget, load_file_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "na-sso.yaml"
    path.write_text(text)
    return path


def test_loads_ordered_repeated_targets_and_environment_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SSH_KEY", "private material")
    path = write(tmp_path, """
version: 1
targets:
  - {id: shell_a, type: ssh, display_name: A, host: a.test, management_user: mgr,
     management_private_key: "${SSH_KEY}", host_key_sha256: "SHA256:AAAAAAAAAAAAAAAAAAAA", platform: ubuntu, mode: key}
  - {id: shell_b, type: ssh, display_name: B, host: b.test, management_user: mgr,
     management_private_key: "${SSH_KEY}", host_key_sha256: "SHA256:BBBBBBBBBBBBBBBBBBBB", platform: rocky, allow_relaxed_usernames: true}
""")
    config = load_file_config(path)
    assert [target.id for target in config.targets] == ["shell_a", "shell_b"]
    assert isinstance(config.targets[0], SshTarget)
    assert config.targets[0].management_private_key.get_secret_value() == "private material"


@pytest.mark.parametrize("text,match", [
    ("targets: [{id: x!, type: opnsense, display_name: X, base_url: x, api_key: k, api_secret: s}]", "target id"),
    ("targets: [{id: same, type: opnsense, display_name: X, base_url: x, api_key: k, api_secret: s}, {id: same, type: nextcloud, display_name: Y, base_url: y, admin_user: a, admin_password: s}]", "unique"),
    ("password_policy: {min_length: 20, max_length: 10}", "max_length"),
    ("ssh_key_policy: {allowed_algorithms: []}", "allowed_algorithms"),
    ("targets: [{id: shell, type: ssh, display_name: S, host: h, management_user: m, management_private_key: k, host_key_sha256: wrong, platform: debian}]", "host_key_sha256"),
    ("targets: [{id: shell, type: ssh, display_name: S, host: h, host_key_sha256: 'SHA256:AAAAAAAAAAAAAAAAAAAA', platform: debian, default_groups: [developers, developers]}]", "default groups"),
    ("targets: [{id: cloud, type: nextcloud, display_name: C, base_url: https://c, default_groups: ['unsafe group']}]", "default groups"),
    ("support_policy: {url: 'javascript:alert(1)'}", "support_policy.url"),
])
def test_rejects_invalid_contract(tmp_path, text, match):
    with pytest.raises(ValidationError, match=match):
        load_file_config(write(tmp_path, text))


def test_missing_secret_reference_is_actionable(tmp_path, monkeypatch):
    monkeypatch.delenv("NOT_SET", raising=False)
    with pytest.raises(ValueError, match="NOT_SET.*targets\\[0\\].api_secret"):
        load_file_config(write(tmp_path, """
targets:
  - {id: firewall, type: opnsense, display_name: Firewall, base_url: https://fw,
     api_key: key, api_secret: "${NOT_SET}"}
"""))


def test_defaults_are_safe_and_complete():
    config = FileConfig()
    assert config.password_policy.history_size == 3
    assert config.password_policy.expiry_acknowledgement_mode == "grace"
    assert config.password_policy.expiry_acknowledgement_grace_days == 14
    assert config.password_policy.expiry_acknowledgement_limit == 1
    assert config.ssh_key_policy.allowed_algorithms == ["ed25519"]
    assert config.audit_policy.retention_days == 365
    assert config.audit_policy.export_page_size == 500
    assert config.admin_mfa_policy.required is False
    assert config.admin_mfa_policy.allowed_methods == ["webauthn", "totp"]
    assert config.notification_policy.enabled is False
    assert config.notification_policy.endpoints == []
    assert config.reconciliation_policy.enabled is False
    assert config.reconciliation_policy.max_users_per_run == 100
    assert config.lifecycle_automation_policy.default_review_interval_days == 90
    assert config.automation_api_policy.enabled is True
    assert config.automation_api_policy.requests_per_minute == 120
    assert config.automation_api_policy.default_token_days == 90
    assert config.automation_api_policy.max_token_days == 365


def test_automation_api_policy_is_bounded():
    policy = FileConfig.model_validate({
        "automation_api_policy": {
            "enabled": False, "requests_per_minute": 10,
            "max_page_size": 250, "idempotency_retention_hours": 48,
            "default_token_days": 30, "max_token_days": 60,
        }
    }).automation_api_policy
    assert not policy.enabled and policy.max_page_size == 250
    for values in (
        {"requests_per_minute": 9}, {"max_page_size": 24},
        {"max_page_size": 501}, {"idempotency_retention_hours": 0},
        {"idempotency_retention_hours": 721},
        {"default_token_days": 91, "max_token_days": 90},
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"automation_api_policy": values})


def test_reconciliation_schedule_and_backoff_are_bounded():
    policy = FileConfig.model_validate({
        "reconciliation_policy": {
            "enabled": True, "interval_seconds": 60, "scan_seconds": 5,
            "retry_base_seconds": 10, "retry_max_seconds": 40,
            "max_attempts": 3, "max_users_per_run": 500,
        }
    }).reconciliation_policy
    assert policy.enabled and policy.retry_max_seconds == 40
    for values in (
        {"interval_seconds": 59},
        {"scan_seconds": 4},
        {"retry_base_seconds": 20, "retry_max_seconds": 10},
        {"max_attempts": 21},
        {"max_users_per_run": 1001},
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"reconciliation_policy": values})


def test_lifecycle_automation_policy_is_bounded():
    policy = FileConfig.model_validate({
        "lifecycle_automation_policy": {
            "scan_seconds": 5, "default_review_interval_days": 30,
            "reminder_days_before_due": 3, "max_review_accounts": 500,
        }
    }).lifecycle_automation_policy
    assert policy.default_review_interval_days == 30
    for values in (
        {"scan_seconds": 4}, {"default_review_interval_days": 0},
        {"reminder_days_before_due": 366}, {"max_review_accounts": 5001},
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"lifecycle_automation_policy": values})


def test_audit_policy_is_bounded_and_can_disable_retention():
    config = FileConfig.model_validate({
        "audit_policy": {"retention_days": None, "export_page_size": 25}
    })
    assert config.audit_policy.retention_days is None
    for values in (
        {"retention_days": 0},
        {"retention_days": 36501},
        {"export_page_size": 24},
        {"export_page_size": 5001},
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"audit_policy": values})


def test_admin_mfa_policy_requires_unique_methods_safe_origin_and_freshness_bounds():
    policy = FileConfig.model_validate({
        "admin_mfa_policy": {
            "required": True,
            "allowed_methods": ["totp"],
            "expected_origin": "https://sso.example.test",
            "reauthentication_minutes": 5,
        }
    }).admin_mfa_policy
    assert policy.required and policy.allowed_methods == ["totp"]
    for values in (
        {"allowed_methods": []},
        {"allowed_methods": ["totp", "totp"]},
        {"allowed_methods": ["sms"]},
        {"expected_origin": "https://sso.example.test/path"},
        {"reauthentication_minutes": 0},
        {"reauthentication_minutes": 61},
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"admin_mfa_policy": values})


def test_notification_policy_requires_signed_safe_unique_bounded_destinations():
    policy = FileConfig.model_validate({
        "notification_policy": {
            "enabled": True,
            "endpoints": [{
                "id": "ops_hook", "url": "https://hooks.example.test/na-sso",
                "secret": "write-only", "events": ["password.expired"],
            }],
        }
    }).notification_policy
    assert policy.endpoints[0].secret.get_secret_value() == "write-only"
    invalid = (
        {"endpoints": [{"id": "hook", "url": "http://remote.test/h", "secret": "s", "events": ["password.expired"]}]},
        {"endpoints": [{"id": "hook", "url": "https://remote.test/h", "secret": "s", "events": []}]},
        {"endpoints": [{"id": "hook", "url": "https://remote.test/h", "secret": "s", "events": ["unknown"]}]},
        {"endpoints": [
            {"id": "same", "url": "https://a.test/h", "secret": "s", "events": ["password.expired"]},
            {"id": "same", "url": "https://b.test/h", "secret": "s", "events": ["password.expired"]},
        ]},
        {"retry_base_seconds": 20, "retry_max_seconds": 10},
        {"max_attempts": 21},
    )
    for values in invalid:
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"notification_policy": values})


def test_password_expiry_acknowledgement_modes_are_explicit_and_validated():
    renewal = FileConfig.model_validate({
        "password_policy": {
            "expiry_acknowledgement_mode": "renewal",
            "expiry_acknowledgement_limit": None,
        }
    })
    assert renewal.password_policy.expiry_acknowledgement_mode == "renewal"
    assert renewal.password_policy.expiry_acknowledgement_limit is None

    disabled = FileConfig.model_validate({
        "password_policy": {"expiry_acknowledgement_mode": "disabled"}
    })
    assert disabled.password_policy.expiry_acknowledgement_mode == "disabled"

    for values in (
        {"expiry_acknowledgement_mode": "forever"},
        {"expiry_acknowledgement_grace_days": 0},
        {"expiry_acknowledgement_limit": 0},
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({"password_policy": values})


def test_ssh_key_and_unmanaged_discovery_policies_are_bounded():
    config = FileConfig.model_validate({
        "ssh_key_policy": {"default_expiry_days": 30, "max_expiry_days": 60},
        "unmanaged_account_policy": {
            "ssh_min_uid": 500, "max_accounts_per_target": 250,
            "excluded_usernames": ["root", "breakglass"],
            "excluded_prefixes": ["svc-"], "allow_removal": True,
        },
    })
    assert config.ssh_key_policy.default_expiry_days == 30
    assert config.unmanaged_account_policy.allow_removal
    for section, values in (
        ("ssh_key_policy", {"default_expiry_days": 90, "max_expiry_days": 30}),
        ("unmanaged_account_policy", {"max_accounts_per_target": 0}),
        ("unmanaged_account_policy", {"excluded_usernames": ["root", "root"]}),
        ("unmanaged_account_policy", {"excluded_prefixes": ["svc-", "svc-"]}),
    ):
        with pytest.raises(ValidationError):
            FileConfig.model_validate({section: values})


def test_ssh_management_auth_allows_password_plus_one_private_key_source():
    target = SshTarget(
        id="shell", type="ssh", display_name="Shell", host="shell",
        management_user="mgr", management_password="secret",
        management_private_key="private-material",
        host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="debian",
    )
    assert target.management_password.get_secret_value() == "secret"
    assert target.management_private_key.get_secret_value() == "private-material"

    with pytest.raises(ValidationError, match="private-key source"):
        SshTarget(
            id="invalid", type="ssh", display_name="Invalid", host="shell",
            management_private_key="inline", management_private_key_file="/key",
            host_key_sha256="SHA256:AAAAAAAAAAAAAAAAAAAA", platform="debian",
        )
