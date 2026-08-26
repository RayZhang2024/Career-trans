import json
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from app.agents.openai_client import create_traced_openai_client
from app.schemas.job import JobProfile
from app.services.job_profile_normalization import normalize_job_profile


class JobExtractionError(RuntimeError):
    """Raised when a job description cannot be converted into a valid JobProfile."""


class JobExtractor(Protocol):
    def extract(self, job_text: str) -> JobProfile:
        """Extract a structured JobProfile from raw job text."""


def _default_prompt_path() -> Path:
    # .../career-trans/backend/app/agents/job_extraction.py -> repo root
    return Path(__file__).resolve().parents[3] / "prompts" / "job_extraction.md"


class OpenAIJobExtractor:
    """OpenAI-backed implementation of JobExtractor.

    The extractor intentionally returns a domain schema rather than provider-specific
    response objects so the provider can be replaced later.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompt_path: Path | None = None,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("An OpenAI API key is required.")

        self._client = client if client is not None else create_traced_openai_client(
            api_key=api_key,
            trace_name="job_extraction",
        )
        self._model = model
        self._prompt_path = prompt_path or _default_prompt_path()

    def extract(self, job_text: str) -> JobProfile:
        prompt = self._load_prompt()
        schema = JobProfile.model_json_schema()

        response = self._client.responses.create(
            model=self._model,
            input=[
                {
                    "role": "system",
                    "content": prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Extract the following job advert into the supplied schema.\n\n"
                        f"JSON schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
                        f"JOB ADVERT:\n{job_text}"
                    ),
                },
            ],
        )

        raw_output = response.output_text.strip()

        # Be tolerant of fenced JSON even though the prompt asks for JSON only.
        if raw_output.startswith("```"):
            raw_output = self._strip_json_fence(raw_output)

        try:
            payload = json.loads(raw_output)
            return normalize_job_profile(JobProfile.model_validate(payload))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise JobExtractionError(
                "The model returned output that did not validate as JobProfile."
            ) from exc

    def _load_prompt(self) -> str:
        try:
            return self._prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise JobExtractionError(
                f"Unable to load job extraction prompt: {self._prompt_path}"
            ) from exc

    @staticmethod
    def _strip_json_fence(text: str) -> str:
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
