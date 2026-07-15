"""Small scriptable client for the versioned NA-SSO automation API."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

import httpx


class CliError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                    "User-Agent": "na-ssoctl/0.1",
                },
                json=payload,
                params=params,
                timeout=self.timeout,
                follow_redirects=False,
            )
        except httpx.HTTPError as error:
            raise CliError(f"API transport failed: {error.__class__.__name__}") from error
        try:
            body = response.json()
        except ValueError as error:
            raise CliError(f"API returned HTTP {response.status_code} without JSON") from error
        if response.status_code >= 400:
            api_error = body.get("error", {}) if isinstance(body, dict) else {}
            message = api_error.get("message", "request failed")
            code = api_error.get("code", "http_error")
            raise CliError(f"API {code} (HTTP {response.status_code}): {message}")
        if not isinstance(body, dict) or "data" not in body:
            raise CliError("API response did not contain the versioned data envelope")
        return body


def _rows_from_file(path: Path) -> list[dict]:
    if not path.is_file():
        raise CliError(f"input file not found: {path}")
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CliError("bulk JSON could not be read") from error
        rows = value.get("rows") if isinstance(value, dict) else value
        if not isinstance(rows, list):
            raise CliError("bulk JSON must be an array or an object containing rows")
        return rows
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise CliError("bulk CSV could not be read as UTF-8") from error
    for row in rows:
        row["target_ids"] = [
            item.strip() for item in str(row.get("target_ids", "")).replace(";", "|").split("|")
            if item.strip()
        ]
    return rows


def _write_output(body: dict, output: str | None = None) -> None:
    if not output:
        print(json.dumps(body, indent=2, sort_keys=True))
        return
    path = Path(output)
    data = body.get("data", [])
    if path.suffix.lower() == ".csv":
        if not isinstance(data, list):
            raise CliError("CSV output requires a list response")
        fieldnames = list(data[0]) if data else ["id"]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(data)
    else:
        path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="na-ssoctl",
        description="Script NA-SSO previews, approved changes, status, and exports.",
    )
    parser.add_argument(
        "--url", "--base-url", dest="url",
        default=os.getenv("NA_SSO_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--token", default=os.getenv("NA_SSO_TOKEN", ""))
    parser.add_argument(
        "--token-file", type=Path,
        help="Read the Bearer credential from a file instead of shell history.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("whoami", help="Show API identity and available resources")
    users = commands.add_parser("users", help="List managed-user summaries")
    users.add_argument("--query", default="")
    users.add_argument("--page", type=int, default=1)
    users.add_argument("--per-page", type=int, default=50)
    commands.add_parser("targets", help="List target capabilities and health")

    bulk_preview = commands.add_parser("bulk-preview", help="Validate a CSV/JSON job without mutation")
    bulk_preview.add_argument("file", type=Path)
    bulk_preview.add_argument("--idempotency-key", required=True)
    bulk_apply = commands.add_parser("bulk-apply", help="Execute a saved bulk preview")
    bulk_apply.add_argument("workflow_id")
    bulk_apply.add_argument("--idempotency-key", required=True)

    status = commands.add_parser("operation-status", help="Show one correlated operation")
    status.add_argument("operation_id")

    reconcile_preview = commands.add_parser("reconcile-preview", help="Create a read-only drift preview")
    reconcile_preview.add_argument("--user-id", type=int)
    reconcile_preview.add_argument("--target-id")
    reconcile_preview.add_argument("--idempotency-key", required=True)
    reconcile_apply = commands.add_parser("reconcile-apply", help="Approve a saved reconciliation preview")
    reconcile_apply.add_argument("run_id")
    reconcile_apply.add_argument("--approval-token", required=True)
    reconcile_apply.add_argument("--idempotency-key", required=True)
    reconcile_apply.add_argument("--confirm-destructive", action="store_true")

    audit = commands.add_parser("audit-export", help="Export one filtered audit page as JSON or CSV")
    audit.add_argument("--output", required=True)
    audit.add_argument("--actor", default="")
    audit.add_argument("--subject", default="")
    audit.add_argument("--action", default="")
    audit.add_argument("--operation", default="")
    audit.add_argument("--page", type=int, default=1)
    audit.add_argument("--per-page", type=int, default=100)
    return parser


def run(args: argparse.Namespace, client: ApiClient) -> dict:
    if args.command == "whoami":
        return client.request("GET", "/api/v1")
    if args.command == "users":
        return client.request("GET", "/api/v1/users", params={
            "q": args.query, "page": args.page, "per_page": args.per_page,
        })
    if args.command == "targets":
        return client.request("GET", "/api/v1/targets")
    if args.command == "bulk-preview":
        return client.request("POST", "/api/v1/bulk/preview", payload={
            "idempotency_key": args.idempotency_key,
            "rows": _rows_from_file(args.file),
        })
    if args.command == "bulk-apply":
        return client.request(
            "POST", f"/api/v1/bulk/{args.workflow_id}/execute",
            payload={"idempotency_key": args.idempotency_key},
        )
    if args.command == "operation-status":
        return client.request("GET", f"/api/v1/operations/{args.operation_id}")
    if args.command == "reconcile-preview":
        return client.request("POST", "/api/v1/reconciliation/preview", payload={
            "idempotency_key": args.idempotency_key,
            "user_id": args.user_id,
            "target_id": args.target_id,
        })
    if args.command == "reconcile-apply":
        return client.request(
            "POST", f"/api/v1/reconciliation/{args.run_id}/approve",
            payload={
                "idempotency_key": args.idempotency_key,
                "approval_token": args.approval_token,
                "confirm_destructive": args.confirm_destructive,
            },
        )
    if args.command == "audit-export":
        return client.request("GET", "/api/v1/audit", params={
            "actor": args.actor, "subject": args.subject, "action": args.action,
            "operation": args.operation, "page": args.page, "per_page": args.per_page,
        })
    raise CliError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.token_file:
        try:
            args.token = args.token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            parser.error(f"token file could not be read: {error.__class__.__name__}")
    if not args.token:
        parser.error("provide --token, --token-file, or set NA_SSO_TOKEN")
    if not args.url.startswith(("http://", "https://")):
        parser.error("--url must use http:// or https://")
    try:
        body = run(args, ApiClient(args.url, args.token, args.timeout))
        _write_output(body, args.output if args.command == "audit-export" else None)
        return 0
    except CliError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
