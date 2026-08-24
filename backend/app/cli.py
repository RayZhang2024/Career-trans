"""HTTP-only command-line client for the running Career-trans API."""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.cli_http import (
    CareerTransApiClient,
    CareerTransApiError,
    CareerTransConfigurationError,
    CareerTransConnectionError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="career-trans", description="Career-trans API client")
    parser.add_argument("--base-url", default=os.getenv("CAREER_TRANS_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.getenv("CAREER_TRANS_TOKEN"))
    commands = parser.add_subparsers(dest="command", required=True)

    auth = commands.add_parser("auth", help="Authenticate with Career-trans")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_commands.add_parser("login", help="Log in and print a bearer token")
    login.add_argument("--email", required=True)

    config = commands.add_parser("config", help="Inspect running semantic LLM configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show", help="Show safe effective LLM configuration")
    config_commands.add_parser("check", help="Validate semantic LLM configuration without a live provider call")

    profile = commands.add_parser("profile", help="Inspect authenticated candidate profile state")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("context-summary", help="Show confirmed candidate-context readiness")

    cv = commands.add_parser("cv", help="Manage CV-ingestion drafts")
    cv_commands = cv.add_subparsers(dest="cv_command", required=True)
    upload = cv_commands.add_parser("upload", help="Upload CV files")
    upload.add_argument("files", nargs="+", type=Path)
    interpret = cv_commands.add_parser("interpret", help="Interpret an uploaded CV draft")
    interpret.add_argument("draft_id")
    show = cv_commands.add_parser("show", help="Show a CV review draft")
    show.add_argument("draft_id")
    show.add_argument("--json", action="store_true", dest="as_json")
    edit = cv_commands.add_parser("edit", help="Replace a review draft with corrected JSON")
    edit.add_argument("draft_id")
    edit.add_argument("--file", required=True, type=Path)
    confirm = cv_commands.add_parser("confirm", help="Confirm a review draft and persist it")
    confirm.add_argument("draft_id")
    confirm.add_argument("--yes", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = CareerTransApiClient(args.base_url, args.token)
    try:
        if args.command == "auth":
            return _login(client, args)
        if args.command == "config":
            return _config(client, args)
        if args.command == "profile":
            return _profile(client, args)
        return _cv(client, args)
    except (CareerTransApiError, CareerTransConnectionError, CareerTransConfigurationError) as exc:
        _print_api_error(exc)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def _login(client: CareerTransApiClient, args: argparse.Namespace) -> int:
    password = getpass.getpass("Password: ")
    response = client.login(args.email, password)
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise CareerTransApiError(502, "Career-trans login response did not include an access token.")
    print(token)
    print(f"$env:CAREER_TRANS_TOKEN = '{token}'")
    return 0


def _cv(client: CareerTransApiClient, args: argparse.Namespace) -> int:
    if args.cv_command == "upload":
        missing = [str(path) for path in args.files if not path.is_file()]
        if missing:
            raise ValueError(f"Local CV file not found: {', '.join(missing)}")
        draft = client.upload_cv(args.files)
        documents = draft.get("documents", [])
        filenames = [item.get("provenance", {}).get("filename", "upload") for item in documents if isinstance(item, dict)]
        print(f"Draft {draft.get('id')} ({draft.get('state')})")
        print(f"Files: {', '.join(filenames)}")
        print(f"Next: career-trans cv interpret {draft.get('id')}")
        return 0
    if args.cv_command == "interpret":
        _print_counts(client.interpret_cv(args.draft_id))
        return 0
    if args.cv_command == "show":
        draft = client.get_cv(args.draft_id)
        if args.as_json:
            print(json.dumps(draft, indent=2, sort_keys=True))
        else:
            _show_human(draft)
        return 0
    if args.cv_command == "edit":
        try:
            corrected = json.loads(args.file.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Corrected review JSON file not found: {args.file}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid review JSON: {exc.msg}") from exc
        _print_counts(client.edit_cv(args.draft_id, corrected))
        return 0
    if not args.yes:
        answer = input("Persist this reviewed CV to your candidate profile? [y/N]: ").strip().casefold()
        if answer not in {"y", "yes"}:
            print("Confirmation cancelled.")
            return 0
    result = client.confirm_cv(args.draft_id)
    print(f"Confirmed {result.get('draft_id', args.draft_id)}; added {result.get('confirmed_evidence_count', 0)} evidence records.")
    return 0


def _config(client: CareerTransApiClient, args: argparse.Namespace) -> int:
    config = client.get_llm_configuration() if args.config_command == "show" else client.check_llm_configuration()
    for key, value in config.items():
        print(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
    return 0


def _profile(client: CareerTransApiClient, args: argparse.Namespace) -> int:
    if args.profile_command == "context-summary":
        for key, value in client.get_candidate_context_summary().items():
            print(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
    return 0


def _print_counts(draft: dict[str, Any]) -> None:
    merged = draft.get("merged") or {}
    print(f"Draft {draft.get('id')} ({draft.get('state')})")
    for field in ("employment", "education", "skills", "projects", "achievements", "evidence"):
        print(f"{field}: {len(merged.get(field, []))}")


def _show_human(draft: dict[str, Any]) -> None:
    print(f"Draft: {draft.get('id')}\nState: {draft.get('state')}")
    documents = draft.get("documents", [])
    if documents:
        print("Files:")
        for document in documents:
            provenance = document.get("provenance", {})
            print(f"- {provenance.get('filename', 'upload')} ({len(document.get('segments', []))} segments)")
    _print_counts(draft)


def _print_api_error(exc: Exception) -> None:
    if isinstance(exc, CareerTransConnectionError):
        message = f"Server unavailable: {exc} Start the API or set CAREER_TRANS_BASE_URL."
    elif isinstance(exc, CareerTransConfigurationError):
        message = str(exc)
    elif isinstance(exc, CareerTransApiError):
        if exc.status_code in {401, 403}:
            message = f"Authentication failed: {exc.detail} Use career-trans auth login or set CAREER_TRANS_TOKEN."
        elif exc.status_code == 404:
            message = f"Not found: {exc.detail} Check the draft ID."
        elif exc.status_code in {409, 422}:
            message = f"Request rejected: {exc.detail} Check the draft state or review data."
        elif exc.status_code == 503:
            message = f"LLM provider configuration or availability error: {exc.detail}"
        elif exc.status_code == 502:
            message = f"Semantic provider error: {exc.detail}"
        elif exc.status_code >= 500:
            message = f"Server error: {exc.detail} Try again later."
        else:
            message = f"API error: {exc.detail}"
    else:
        message = str(exc)
    print(f"Error: {message}", file=sys.stderr)
