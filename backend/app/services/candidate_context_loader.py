import re
from pathlib import Path

from app.schemas.candidate import (
    CandidateContext,
    CandidateEligibility,
    CareerEvidence,
)


_EVIDENCE_HEADING = re.compile(
    r"^## (?P<id>[A-Z0-9-]+) — (?P<title>.+?)\s*$",
    re.MULTILINE,
)
_SKILLS_SECTION = re.compile(
    r"### Skills Demonstrated\s*\n(?P<body>.*?)(?=\n### |\n---|\Z)",
    re.DOTALL,
)


class CandidateContextLoadError(RuntimeError):
    """Raised when a candidate context cannot be loaded from a resource directory."""


class MarkdownCandidateContextLoader:
    """Load repository demo/profile Markdown into the generic CandidateContext schema.

    This loader exists for development, evaluation, and migration tooling. Production
    user data should later come from authenticated persistence services, not from the
    repository filesystem.
    """

    def load(self, profile_dir: Path) -> CandidateContext:
        if not profile_dir.exists() or not profile_dir.is_dir():
            raise CandidateContextLoadError(
                f"Candidate profile directory does not exist: {profile_dir}"
            )

        profile_text = self._read_optional(profile_dir / "01_candidate_profile.md")
        evidence_text = self._read_optional(
            profile_dir / "02_master_career_evidence.md"
        )
        skills_text = self._read_optional(profile_dir / "05_skills_matrix.md")
        strategy_text = self._read_optional(profile_dir / "06_career_strategy.md")
        criteria_text = self._read_optional(
            profile_dir / "07_job_search_criteria.md"
        )

        if not any(
            [profile_text, evidence_text, skills_text, strategy_text, criteria_text]
        ):
            raise CandidateContextLoadError(
                f"No supported candidate resource files found in: {profile_dir}"
            )

        return CandidateContext(
            source_name=profile_dir.name,
            profile_text=profile_text,
            skills_text=skills_text,
            career_strategy_text=strategy_text,
            job_search_criteria_text=criteria_text,
            eligibility=self._extract_eligibility(profile_text),
            evidence=self._parse_evidence(evidence_text),
        )

    @staticmethod
    def _read_optional(path: Path) -> str:
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CandidateContextLoadError(f"Unable to read: {path}") from exc

    @staticmethod
    def _parse_evidence(markdown: str) -> list[CareerEvidence]:
        if not markdown:
            return []

        matches = list(_EVIDENCE_HEADING.finditer(markdown))
        evidence: list[CareerEvidence] = []

        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
            body = markdown[start:end].strip()

            # Remove the separator that commonly precedes the next evidence record.
            body = re.sub(r"\n---\s*$", "", body).strip()
            skills = MarkdownCandidateContextLoader._extract_skills(body)

            evidence.append(
                CareerEvidence(
                    evidence_id=match.group("id").strip(),
                    title=match.group("title").strip(),
                    text=body,
                    skills=skills,
                )
            )

        return evidence

    @staticmethod
    def _extract_skills(body: str) -> list[str]:
        section = _SKILLS_SECTION.search(body)
        if section is None:
            return []

        skills: list[str] = []
        for line in section.group("body").splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                skill = stripped[2:].strip()
                if skill:
                    skills.append(skill)
        return skills

    @staticmethod
    def _extract_eligibility(profile_text: str) -> CandidateEligibility:
        text = profile_text.lower()

        work_authorisation: list[str] = []

        if "global talent" in text:
            work_authorisation.append("United Kingdom")

        return CandidateEligibility(
            work_authorisation=work_authorisation,
        )