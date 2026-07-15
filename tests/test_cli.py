import json

from na_sso.cli import CliError, main


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"api_version": "v1", "data": []}
        self.error = error
        self.calls = []

    def request(self, method, path, *, payload=None, params=None):
        self.calls.append((method, path, payload, params))
        if self.error:
            raise self.error
        return self.response


def _install(monkeypatch, fake):
    monkeypatch.setattr("na_sso.cli.ApiClient", lambda *args, **kwargs: fake)


def test_cli_bulk_preview_apply_operation_and_reconciliation_commands(
    tmp_path, monkeypatch, capsys,
):
    input_path = tmp_path / "bulk.csv"
    input_path.write_text(
        "username,action,display_name,email,target_ids\n"
        "cli_user,onboard,CLI User,cli@example.test,cloud|nexus\n",
        encoding="utf-8",
    )
    fake = FakeClient({"api_version": "v1", "data": {"id": "workflow"}})
    _install(monkeypatch, fake)
    assert main([
        "--token", "not-printed", "bulk-preview", str(input_path),
        "--idempotency-key", "cli-preview-0001",
    ]) == 0
    method, path, payload, _params = fake.calls[-1]
    assert (method, path) == ("POST", "/api/v1/bulk/preview")
    assert payload["rows"][0]["target_ids"] == ["cloud", "nexus"]
    assert "not-printed" not in capsys.readouterr().out

    assert main([
        "--token", "not-printed", "bulk-apply", "workflow",
        "--idempotency-key", "cli-apply-0001",
    ]) == 0
    assert fake.calls[-1][1] == "/api/v1/bulk/workflow/execute"
    assert main([
        "--token", "not-printed", "operation-status", "operation-id",
    ]) == 0
    assert fake.calls[-1][1] == "/api/v1/operations/operation-id"
    assert main([
        "--token", "not-printed", "reconcile-preview", "--user-id", "4",
        "--target-id", "cloud", "--idempotency-key", "cli-reconcile-0001",
    ]) == 0
    assert fake.calls[-1][2]["user_id"] == 4
    assert main([
        "--token", "not-printed", "reconcile-apply", "run-id",
        "--approval-token", "approval-token", "--confirm-destructive",
        "--idempotency-key", "cli-repair-0001",
    ]) == 0
    assert fake.calls[-1][2]["confirm_destructive"] is True


def test_cli_audit_export_writes_json_or_csv(tmp_path, monkeypatch):
    response = {
        "api_version": "v1",
        "data": [{"id": 1, "actor": "service:reader", "action": "audit.test"}],
        "meta": {"page": 1},
    }
    fake = FakeClient(response)
    _install(monkeypatch, fake)
    json_path = tmp_path / "audit.json"
    assert main([
        "--token", "token", "audit-export", "--output", str(json_path),
        "--action", "audit.test",
    ]) == 0
    assert json.loads(json_path.read_text())["data"][0]["action"] == "audit.test"
    assert fake.calls[-1][3]["action"] == "audit.test"

    csv_path = tmp_path / "audit.csv"
    assert main([
        "--token", "token", "audit-export", "--output", str(csv_path),
    ]) == 0
    assert "service:reader,audit.test" in csv_path.read_text()


def test_cli_reports_safe_api_errors_without_token(monkeypatch, capsys):
    fake = FakeClient(error=CliError("API forbidden (HTTP 403): capability missing"))
    _install(monkeypatch, fake)
    token = "never-render-this-token"
    assert main(["--token", token, "whoami"]) == 1
    output = capsys.readouterr()
    assert "API forbidden" in output.err
    assert token not in output.err + output.out


def test_cli_reads_token_file_and_accepts_documented_base_url_alias(
    tmp_path, monkeypatch, capsys,
):
    token = "nas_file_secret_that_must_not_be_printed"
    token_file = tmp_path / "na-sso.token"
    token_file.write_text(token + "\n", encoding="utf-8")
    fake = FakeClient({"api_version": "v1", "data": {"identity": "runner"}})
    captured = {}

    def client(base_url, supplied_token, timeout):
        captured.update(url=base_url, token=supplied_token, timeout=timeout)
        return fake

    monkeypatch.setattr("na_sso.cli.ApiClient", client)

    assert main([
        "--base-url", "http://127.0.0.1:8001",
        "--token-file", str(token_file), "whoami",
    ]) == 0
    assert captured == {
        "url": "http://127.0.0.1:8001", "token": token, "timeout": 30.0,
    }
    output = capsys.readouterr()
    assert token not in output.out + output.err
