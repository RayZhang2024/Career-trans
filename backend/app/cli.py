"""HTTP-only command-line client for the running Career-trans API."""

import argparse
import getpass
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.cli_http import (
    CareerTransApiClient,
    CareerTransApiError,
    CareerTransConfigurationError,
    CareerTransConnectionError,
    CareerTransTimeoutError,
)
from app.schemas.external_discovery import ExternalDiscoverySearchContextResponse
from app.schemas.discovery import JobListing
from app.services.codex_external_discovery_service import (
    CodexExternalDiscoveryError,
    CodexExternalDiscoveryRunner,
)
from app.services.job_deduplication_service import JobDeduplicationService
from app.services.llm_usage_audit import (
    combine_normalized_trace_exports,
    normalize_trace_exports,
    summarize_trace_export,
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

    dev = commands.add_parser("dev", help="Offline development diagnostics")
    dev_commands = dev.add_subparsers(dest="dev_command", required=True)
    audit = dev_commands.add_parser("analyse-llm-trace", help="Summarize an exported LangSmith JSON trace")
    audit.add_argument("--input", required=True, type=Path)
    normalize = dev_commands.add_parser(
        "normalize-llm-traces",
        help="Normalize explicitly labelled LangSmith provider exports for offline analysis",
    )
    normalize.add_argument(
        "--stage",
        action="append",
        required=True,
        metavar="STAGE=PATH",
        help="One exact semantic stage and exported JSON path; repeat for each provider run",
    )
    normalize.add_argument(
        "--funnel",
        "--diagnostics",
        dest="funnel",
        type=Path,
        help="Optional ranking diagnostics JSON; only safe numeric funnel counters are retained",
    )
    normalize.add_argument("--output", type=Path, help="Optional output JSON path (otherwise stdout)")

    profile = commands.add_parser("profile", help="Inspect authenticated candidate profile state")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("context-summary", help="Show confirmed candidate-context readiness")
    profile_commands.add_parser("show", help="Show your persisted profile and career direction")
    set_strategy = profile_commands.add_parser("set-strategy", help="Update your career goal and job-search criteria")
    set_strategy.add_argument("--career-goal")
    set_strategy.add_argument("--job-search-criteria")

    jobs = commands.add_parser("jobs", help="Run authenticated job-discovery workflows")
    jobs_commands = jobs.add_subparsers(dest="jobs_command", required=True)
    discover_external = jobs_commands.add_parser(
        "discover-external",
        help="Use local Codex for broad employer-agnostic search (or target companies optionally), then import vacancies",
    )
    discover_external.add_argument("--keyword", dest="keywords", action="append", required=True)
    discover_external.add_argument("--location", dest="locations", action="append", default=[])
    discover_external.add_argument("--company", dest="companies", action="append", default=[])
    discover_external.add_argument("--max-results", type=int, default=20)
    discover_external.add_argument("--remote-ok", action="store_true", default=None)
    discover_external.add_argument(
        "--rank",
        action="store_true",
        help="Pass imported accepted jobs to the existing authenticated ranking endpoint",
    )
    list_jobs = jobs_commands.add_parser("list", help="List recent persisted external discoveries")
    list_jobs.add_argument("--limit", type=int, default=20)
    rank_imported = jobs_commands.add_parser(
        "rank-imported",
        help="Rank recent persisted external discoveries against your confirmed profile",
    )
    rank_imported.add_argument("--limit", type=int, default=20)
    rank_imported.add_argument(
        "--details",
        action="store_true",
        help="Show existing requirement, fit, career, and recommendation diagnostics",
    )
    enrich_imported = jobs_commands.add_parser(
        "enrich-imported",
        help="Fetch known vacancy URLs to recover usable persisted job detail",
    )
    enrich_imported.add_argument("--limit", type=int, default=20)
    discover_ats = jobs_commands.add_parser(
        "discover-ats",
        help="Scan persisted resolved ATS sources without web search or ranking",
    )
    discover_ats.add_argument("--limit", type=int, default=100)
    discover_ats.add_argument("--max-sources", type=int, default=20)
    discover_ats.add_argument("--provider", dest="providers", action="append", default=[])
    discover_ats.add_argument("--company", dest="companies", action="append", default=[])
    hunt = jobs_commands.add_parser(
        "hunt",
        help="Scan known ATS sources, add local Codex broad discovery, then rank only new or updated jobs",
    )
    hunt.add_argument("--keyword", dest="keywords", action="append", required=True)
    hunt.add_argument("--location", dest="locations", action="append", default=[])
    hunt.add_argument("--max-ats-results", type=int, default=100)
    hunt.add_argument("--max-ats-sources", type=int, default=20)
    hunt.add_argument("--max-external-results", type=int, default=20)
    hunt.add_argument(
        "--max-rank",
        type=int,
        default=10,
        help="Maximum actionable jobs submitted to bounded semantic screening (default: 10)",
    )
    hunt.add_argument(
        "--max-full-analyses",
        type=int,
        default=5,
        help="Maximum relevance-qualified jobs sent to deep career analysis (default: 5)",
    )
    hunt.add_argument(
        "--no-external",
        action="store_true",
        help="Skip local Codex broad discovery and run the structured ATS scan only",
    )

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
    if args.command == "dev":
        return _dev(args)
    client = CareerTransApiClient(args.base_url, args.token)
    try:
        if args.command == "auth":
            return _login(client, args)
        if args.command == "config":
            return _config(client, args)
        if args.command == "profile":
            return _profile(client, args)
        if args.command == "jobs":
            return _jobs(client, args)
        return _cv(client, args)
    except (CareerTransApiError, CareerTransConnectionError, CareerTransConfigurationError, CodexExternalDiscoveryError) as exc:
        _print_api_error(exc)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def _dev(args: argparse.Namespace) -> int:
    if args.dev_command == "analyse-llm-trace":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        print(summarize_trace_export(payload).model_dump_json(indent=2))
        return 0
    if args.dev_command == "normalize-llm-traces":
        exports: list[tuple[str, Any]] = []
        for specification in args.stage:
            stage, separator, path_text = specification.partition("=")
            if not separator or not stage or not path_text:
                raise ValueError("Each --stage must use STAGE=PATH.")
            exports.append((stage, json.loads(Path(path_text).read_text(encoding="utf-8"))))
        normalized = normalize_trace_exports(exports)
        if args.funnel:
            funnel = json.loads(args.funnel.read_text(encoding="utf-8"))
            normalized = combine_normalized_trace_exports(normalized, funnel)
        rendered = json.dumps(normalized, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0
    raise ValueError(f"Unsupported dev command: {args.dev_command}")


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
    elif args.profile_command == "show":
        _print_profile(client.get_profile())
    elif args.profile_command == "set-strategy":
        updates = {
            key: value
            for key, value in {
                "career_goal": args.career_goal,
                "job_search_criteria": args.job_search_criteria,
            }.items()
            if value is not None
        }
        if not updates:
            raise ValueError("Provide --career-goal and/or --job-search-criteria.")
        try:
            client.get_profile()
        except CareerTransApiError as exc:
            if exc.status_code != 404:
                raise
            _print_profile(client.create_profile(updates))
        else:
            _print_profile(client.update_profile(updates))
    return 0


def _print_profile(profile: dict[str, Any]) -> None:
    print(f"career_goal={profile.get('career_goal') or ''}")
    print(f"job_search_criteria={profile.get('job_search_criteria') or ''}")


def _jobs(client: CareerTransApiClient, args: argparse.Namespace) -> int:
    if args.jobs_command == "hunt":
        return _hunt(client, args)
    if args.jobs_command == "discover-ats":
        response = client.discover_known_ats_sources(
            {
                "max_results": args.limit,
                "max_sources": args.max_sources,
                "providers": args.providers,
                "companies": args.companies,
            }
        )
        print(
            f"ATS scan: {len(response.get('listings', []))} jobs "
            f"(raw={response.get('raw_count', 0)}, rejected={response.get('rejected_count', 0)}, "
            f"deduplicated={response.get('deduplicated_count', 0)})."
        )
        for item in response.get("source_diagnostics", []):
            status = "OK" if item.get("succeeded") else "FAILED"
            print(
                f"{status} | {item.get('company')} | {item.get('provider')}:{item.get('source_token')} | "
                f"discovered={item.get('discovered_count', 0)} imported={item.get('imported_count', 0)} "
                f"unchanged={item.get('unchanged_count', 0)} updated={item.get('updated_count', 0)} "
                f"deduplicated={item.get('deduplicated_count', 0)} "
                f"bounded_out={item.get('bounded_out_count', 0)} rejected={item.get('rejected_count', 0)}"
            )
            if item.get("failure_kind"):
                print(f"Failure kind: {item['failure_kind']}")
        return 0
    if args.jobs_command == "list":
        inbox = client.get_opportunity_inbox(args.limit)
        for item in inbox.get("jobs", []):
            job = item.get("job", {})
            print(f"{job.get('title', 'Untitled')} | {job.get('company') or 'Unknown company'} | {job.get('location') or 'Unknown location'}")
            print(job.get("url", ""))
        return 0
    if args.jobs_command == "rank-imported":
        inbox = client.get_opportunity_inbox(args.limit)
        jobs = [item["job"] for item in inbox.get("jobs", []) if isinstance(item, dict) and isinstance(item.get("job"), dict)]
        if not jobs:
            print("No persisted external discoveries to rank.")
            return 0
        ranking = client.rank_jobs_for_current_user(jobs)
        for result in ranking.get("results", []):
            job = result.get("job", {})
            relevance = result.get("relevance", {})
            recommendation = result.get("recommendation_assessment", {})
            archetype = result.get("archetype", {})
            print(
                f"#{result.get('rank')} {recommendation.get('recommendation', 'unknown').upper()} | "
                f"{job.get('title', 'Untitled')} | {job.get('company') or 'Unknown company'} | "
                f"{job.get('location') or 'Unknown location'}"
            )
            print(
                f"URL: {job.get('url', '')}\n"
                f"Relevance: {relevance.get('score', 'n/a')} | "
                f"Fit: {recommendation.get('fit_score', 'n/a')} | "
                f"Career alignment: {recommendation.get('career_alignment_score', 'n/a')} | "
                f"Archetype: {archetype.get('archetype', 'unknown')}"
            )
            if recommendation.get("reasoning"):
                print(f"Rationale: {recommendation['reasoning']}")
            if args.details:
                _print_ranking_details(result)
        _print_ranking_funnel(ranking)
        _print_unranked_job_diagnostics(ranking)
        if not ranking.get("results", []):
            print("No ranked results were produced; see the ranking funnel and job diagnostics above.")
        return 0
    if args.jobs_command == "enrich-imported":
        response = client.enrich_imported_jobs(args.limit)
        for outcome in response.get("outcomes", []):
            status = str(outcome.get("status", "failed")).upper()
            print(
                f"{status} | {outcome.get('title', 'Untitled')} | "
                f"{outcome.get('company') or 'Unknown company'}"
            )
            if outcome.get("reason"):
                print(f"Reason: {outcome['reason']}")
        return 0
    query: dict[str, Any] = {
        "keywords": args.keywords,
        "locations": args.locations,
        "companies": args.companies,
        "max_results": args.max_results,
    }
    if args.remote_ok is not None:
        query["remote_ok"] = args.remote_ok
    context = ExternalDiscoverySearchContextResponse.model_validate(
        client.get_external_discovery_search_context(query)
    )
    jobs = CodexExternalDiscoveryRunner().discover(context)
    imported = _import_codex_discoveries(client, jobs, query)
    scope = "broad employer-agnostic" if not args.companies else "company-targeted"
    lifecycle = imported.get("lifecycle_counts", {})
    print(
        f"Imported {len(imported.get('accepted_jobs', []))} jobs ({scope}; "
        f"raw={len(jobs)}, normalized={len(jobs)}, rejected={imported.get('rejected_count', 0)}, "
        f"deduplicated={imported.get('deduplicated_count', 0)}, bounded_out={imported.get('bounded_out_count', 0)}, "
        f"new={lifecycle.get('new', 0)}, updated={lifecycle.get('updated', 0)}, "
        f"unchanged={lifecycle.get('unchanged', 0)})."
    )
    if args.rank and imported.get("accepted_jobs"):
        ranking = client.rank_jobs_for_current_user(imported["accepted_jobs"])
        print(
            f"Ranked {ranking.get('discovered_count', 0)} jobs "
            f"(finalists={ranking.get('finalist_count', 0)}, "
            f"analysed={ranking.get('analysed_count', 0)})."
        )
    return 0


def _hunt(client: CareerTransApiClient, args: argparse.Namespace) -> int:
    """Compose existing acquisition APIs and rank only current-run actionable jobs."""
    ats: dict[str, Any] | None = None
    external: dict[str, Any] | None = None
    failures: list[str] = []
    try:
        ats = client.discover_known_ats_sources(
            {
                "max_results": args.max_ats_results,
                "max_sources": args.max_ats_sources,
                "locations": args.locations,
                "keywords": args.keywords,
            }
        )
    except (CareerTransApiError, CareerTransConnectionError, CareerTransConfigurationError) as exc:
        failures.append(f"ATS acquisition failed: {exc}")

    query: dict[str, Any] = {
        "keywords": args.keywords,
        "locations": args.locations,
        "max_results": args.max_external_results,
    }
    if not args.no_external:
        try:
            context = ExternalDiscoverySearchContextResponse.model_validate(
                client.get_external_discovery_search_context(query)
            )
            jobs = CodexExternalDiscoveryRunner().discover(context)
            external = _import_codex_discoveries(client, jobs, query)
        except (CareerTransApiError, CareerTransConnectionError, CareerTransConfigurationError, CodexExternalDiscoveryError) as exc:
            failures.append(f"Codex acquisition failed: {exc}")

    actionable = _actionable_hunt_jobs(ats, external)
    deduplicated, duplicate_count = JobDeduplicationService().deduplicate(actionable)
    bounded = deduplicated[: args.max_rank]
    _print_hunt_acquisition_summary(ats, external, len(actionable), duplicate_count, len(bounded), failures)
    if not bounded:
        print("No new or updated opportunities; semantic ranking skipped.")
        return 0 if ats is not None or external is not None else 2

    try:
        deep_analysis_budget = min(args.max_full_analyses, len(bounded))
        ranking = client.rank_jobs_for_current_user(
            [job.model_dump(mode="json") for job in bounded],
            max_full_analyses=deep_analysis_budget,
        )
    except (CareerTransApiError, CareerTransConnectionError, CareerTransConfigurationError, CareerTransTimeoutError) as exc:
        print(f"Ranking failed: {exc}")
        return 2
    print(
        f"Ranked {ranking.get('discovered_count', 0)} actionable jobs "
        f"(finalists={ranking.get('finalist_count', 0)}, analysed={ranking.get('analysed_count', 0)})."
    )
    for result in ranking.get("results", [])[:deep_analysis_budget]:
        job = result.get("job", {})
        recommendation = result.get("recommendation_assessment", {})
        print(
            f"#{result.get('rank')} {str(recommendation.get('recommendation', 'unknown')).upper()} | "
            f"{job.get('title', 'Untitled')} | {job.get('company') or 'Unknown company'}"
        )
    return 0


def _actionable_hunt_jobs(
    ats: dict[str, Any] | None,
    external: dict[str, Any] | None,
) -> list[JobListing]:
    """Select exact current-run NEW/UPDATED listings from existing response read models."""
    actionable: list[JobListing] = []
    for response in (ats, external):
        if not response:
            continue
        for item in response.get("lifecycle_jobs", []):
            if not isinstance(item, dict) or item.get("state") not in {"new", "updated"}:
                continue
            job = item.get("job")
            if isinstance(job, dict):
                actionable.append(JobListing.model_validate(job))
    return actionable


def _print_hunt_acquisition_summary(
    ats: dict[str, Any] | None,
    external: dict[str, Any] | None,
    actionable_count: int,
    duplicate_count: int,
    bounded_count: int,
    failures: list[str],
) -> None:
    if ats is not None:
        lifecycle = ats.get("lifecycle_counts", {})
        ats_failure_kinds = Counter(
            str(item["failure_kind"])
            for item in ats.get("source_diagnostics", [])
            if isinstance(item, dict) and not item.get("succeeded", False) and item.get("failure_kind")
        )
        failure_summary = " ".join(
            f"{kind}={count}" for kind, count in sorted(ats_failure_kinds.items())
        )
        print(
            f"ATS: sources={len(ats.get('source_diagnostics', []))} raw={ats.get('raw_count', 0)} "
            f"new={lifecycle.get('new', 0)} updated={lifecycle.get('updated', 0)} "
            f"unchanged={lifecycle.get('unchanged', 0)} failures="
            f"{sum(not item.get('succeeded', False) for item in ats.get('source_diagnostics', []) if isinstance(item, dict))}"
            f"{f' ({failure_summary})' if failure_summary else ''}."
        )
    if external is not None:
        lifecycle = external.get("lifecycle_counts", {})
        print(
            f"Codex: accepted={len(external.get('accepted_jobs', []))} "
            f"new={lifecycle.get('new', 0)} updated={lifecycle.get('updated', 0)} "
            f"unchanged={lifecycle.get('unchanged', 0)}."
        )
    print(
        f"Actionable before cross-channel dedup={actionable_count}; "
        f"deduplicated={duplicate_count}; bounded_for_ranking={bounded_count}."
    )
    for failure in failures:
        print(failure)


def _import_codex_discoveries(
    client: CareerTransApiClient,
    jobs: list[Any],
    query: dict[str, Any],
) -> dict[str, Any]:
    """Keep all local Codex execution in the existing runner and import via the shared API."""
    return client.import_discovered_jobs(
        runtime="codex",
        jobs=[job.model_dump(mode="json") for job in jobs],
        query=query,
    )


def _print_counts(draft: dict[str, Any]) -> None:
    merged = draft.get("merged") or {}
    print(f"Draft {draft.get('id')} ({draft.get('state')})")
    for field in ("employment", "education", "skills", "projects", "achievements", "evidence"):
        print(f"{field}: {len(merged.get(field, []))}")


def _print_ranking_details(result: dict[str, Any]) -> None:
    """Render existing ranking response fields only; this performs no extra analysis."""
    print("Requirements")
    matches = result.get("requirement_matches", [])
    if not matches:
        print("- No retained requirement matches.")
    for match in matches:
        requirement = match.get("requirement", {})
        evidence_ids = ", ".join(match.get("evidence_ids", [])) or "none retained"
        references = [
            reference.get("source_ref", "")
            for reference in match.get("evidence_refs", [])
            if isinstance(reference, dict) and reference.get("source_ref")
        ]
        evidence = evidence_ids
        if references:
            evidence = f"{evidence}; refs: {', '.join(references)}"
        print(
            f"- [{str(match.get('match_type', 'unknown')).upper()}] "
            f"{requirement.get('text', 'Unspecified requirement')} "
            f"({requirement.get('importance', 'unspecified')}, score={match.get('score', 'n/a')}) "
            f"— evidence: {evidence}"
        )
        if match.get("reasoning"):
            print(f"  Reasoning: {match['reasoning']}")

    fit = result.get("fit_assessment", {})
    essential_score = fit.get("essential_score")
    desirable_score = fit.get("desirable_score")
    print("Fit drivers")
    print(
        f"- total={fit.get('fit_score', 'n/a')}; essential="
        f"{'None' if essential_score is None else essential_score}; "
        f"desirable={'None' if desirable_score is None else desirable_score}; "
        f"strengths={fit.get('strengths', [])}"
    )
    print(f"- hard blockers: {fit.get('hard_blockers', [])}")
    for gap in fit.get("gaps", []):
        requirement = gap.get("requirement", {})
        print(
            f"- {gap.get('gap_type', 'gap')} [{gap.get('severity', 'unknown')}]: "
            f"{requirement.get('text', 'Unspecified requirement')} — {gap.get('reason', '')}"
        )

    career = result.get("career_assessment", {})
    print("Career drivers")
    print(f"- score={career.get('career_alignment_score', 'n/a')}; confidence={career.get('confidence', 'unknown')}")
    for strength in career.get("strategic_strengths", []):
        print(f"+ {strength}")
    for tradeoff in career.get("strategic_tradeoffs", []):
        print(f"- {tradeoff}")
    if career.get("reasoning"):
        print(f"Reasoning: {career['reasoning']}")

    recommendation = result.get("recommendation_assessment", {})
    print("Recommendation rule")
    print(f"- rule_id: {recommendation.get('rule_id', 'unknown')}")
    print(f"- strengths: {recommendation.get('key_strengths', [])}")
    print(f"- trade-offs: {recommendation.get('key_tradeoffs', [])}")
    print(f"- hard blockers: {recommendation.get('hard_blockers', [])}")
    if recommendation.get("reasoning"):
        print(f"- reasoning: {recommendation['reasoning']}")


def _print_ranking_funnel(ranking: dict[str, Any]) -> None:
    """Render existing response-level counters without adding ranking decisions."""
    print(
        "Ranking funnel: "
        f"discovered={ranking.get('discovered_count', 0)}, "
        f"gated_out={ranking.get('gated_out_count', 0)}, "
        f"relevance_screened={ranking.get('relevance_screened_count', 0)}, "
        f"finalists={ranking.get('finalist_count', 0)}, "
        f"analysed={ranking.get('analysed_count', 0)}."
    )


def _print_unranked_job_diagnostics(ranking: dict[str, Any]) -> None:
    """Explain existing non-result diagnostics without exposing raw inputs/errors."""
    ranked_job_keys = {
        _job_key(result.get("job", {}))
        for result in ranking.get("results", [])
        if isinstance(result, dict) and isinstance(result.get("job"), dict)
    }
    screening_by_url = {
        _job_key(item.get("job", {})): item
        for item in ranking.get("semantic_screening", [])
        if isinstance(item, dict) and isinstance(item.get("job"), dict)
    }
    failure_keys: set[str] = set()
    for failure in ranking.get("failures", []):
        if not isinstance(failure, dict) or not isinstance(failure.get("job"), dict):
            continue
        job = failure.get("job", {})
        key = _job_key(job)
        failure_keys.add(key)
        if key in ranked_job_keys:
            continue
        screening = screening_by_url.get(key, {})
        relevance = screening.get("relevance", {})
        archetype = screening.get("archetype", {})
        if failure.get("stage") == "insufficient_job_detail":
            print(f"UNASSESSED | {_job_label(job)}")
        else:
            print(f"FAILED | {failure.get('stage', 'ranking')} | {_job_label(job)}")
        _print_semantic_context(relevance, archetype)
        print(f"Reason: {failure.get('error', 'Ranking stage failed.')}")

    for item in ranking.get("semantic_screening", []):
        if not isinstance(item, dict) or not isinstance(item.get("job"), dict):
            continue
        job = item["job"]
        key = _job_key(job)
        if key in ranked_job_keys or key in failure_keys:
            continue
        relevance = item.get("relevance", {})
        archetype = item.get("archetype", {})
        if item.get("failure_stage"):
            print(f"FAILED | {item['failure_stage']} | {_job_label(job)}")
            _print_semantic_context(relevance, archetype)
            print(f"Reason: {item.get('error', 'Semantic screening failed.')}")
        elif isinstance(relevance, dict) and relevance:
            if relevance.get("relevant") is False:
                outcome = "semantic relevance decision was relevant=false"
            elif relevance.get("relevant") is True and not archetype:
                outcome = "relevance score was below the configured threshold"
            elif relevance.get("relevant") is True:
                outcome = "the job was not retained for bounded deep analysis"
            else:
                outcome = "semantic screening did not retain the job"
            print(f"SCREENED OUT | {_job_label(job)}")
            _print_semantic_context(relevance, archetype)
            print(f"Reason: {outcome}.")
            if relevance.get("reasoning"):
                print(f"Rationale: {relevance['reasoning']}")
        else:
            print(f"NOT ANALYSED | {_job_label(job)}")
            print("Reason: the job was not retained for bounded deep analysis.")


def _print_semantic_context(relevance: object, archetype: object) -> None:
    relevance_data = relevance if isinstance(relevance, dict) else {}
    archetype_data = archetype if isinstance(archetype, dict) else {}
    line = (
        f"Relevance: {relevance_data.get('score', 'n/a')} | "
        f"Archetype: {archetype_data.get('archetype', 'unknown')}"
    )
    if "relevant" in relevance_data:
        line += f" | Relevant: {str(relevance_data['relevant']).lower()}"
    print(line)


def _job_key(job: dict[str, Any]) -> str:
    return str(job.get("url") or "|".join(str(job.get(key, "")) for key in ("source", "external_id", "title", "company")))


def _job_label(job: dict[str, Any]) -> str:
    return (
        f"{job.get('title', 'Untitled')} | {job.get('company') or 'Unknown company'} | "
        f"{job.get('location') or 'Unknown location'}"
    )


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
    if isinstance(exc, CareerTransTimeoutError):
        message = str(exc)
    elif isinstance(exc, CareerTransConnectionError):
        message = f"Server unavailable: {exc} Start the API or set CAREER_TRANS_BASE_URL."
    elif isinstance(exc, CareerTransConfigurationError):
        message = str(exc)
    elif isinstance(exc, CareerTransApiError):
        if exc.status_code in {401, 403}:
            message = f"Authentication failed: {exc.detail} Use career-trans auth login or set CAREER_TRANS_TOKEN."
        elif exc.status_code == 404:
            message = f"Not found: {exc.detail}"
            if "CV ingestion draft" in exc.detail:
                message += " Check the draft ID."
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
