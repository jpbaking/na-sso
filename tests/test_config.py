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
    assert config.ssh_key_policy.allowed_algorithms == ["ed25519"]


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
