"""Public Workday CXS job-detail recovery for an already-known vacancy URL."""

import json
import re
from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit, urlunsplit

from app.providers.page_fetch import PageFetcher
from app.schemas.agentic_discovery import ExtractedVacancy, PageContent


class WorkdayJobDetailExtractor:
    """Map Workday's public CXS detail response into the shared vacancy schema.

    Workday's public vacancy pages are often JavaScript shells. The shell
    identifies a tenant and career site whose public CXS detail endpoint is
    used by the same page to retrieve the factual job posting.
    """

    _workday_host = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", re.IGNORECASE)
    _config_value = r'\b{key}\s*:\s*"(?P<value>[A-Za-z0-9_-]+)"'

    def __init__(self, page_fetcher: PageFetcher) -> None:
        self._page_fetcher = page_fetcher

    def extract(self, page: PageContent) -> ExtractedVacancy | None:
        detail_url = self._detail_url(page)
        if detail_url is None:
            return None
        try:
            detail_page = self._page_fetcher.fetch(detail_url)
            payload = json.loads(detail_page.html)
        except (ValueError, json.JSONDecodeError):
            return None
        return self._vacancy(payload)

    @classmethod
    def _detail_url(cls, page: PageContent) -> str | None:
        parts = urlsplit(page.final_url)
        host = (parts.hostname or "").casefold()
        host_match = cls._workday_host.fullmatch(host)
        if parts.scheme not in {"http", "https"} or host_match is None:
            return None

        tenant = cls._config(page.html, "tenant")
        site_id = cls._config(page.html, "siteId")
        if tenant is None or site_id is None or tenant.casefold() != host_match.group("tenant").casefold():
            return None

        marker = "/job/"
        marker_index = parts.path.casefold().find(marker)
        if marker_index < 0:
            return None
        external_path = parts.path[marker_index + len(marker) :].strip("/")
        if not external_path:
            return None

        endpoint_path = (
            f"/wday/cxs/{quote(tenant, safe='-._~')}/{quote(site_id, safe='-._~')}"
            f"/job/{quote(external_path, safe='/-._~')}"
        )
        return urlunsplit((parts.scheme, parts.netloc, endpoint_path, "", ""))

    @classmethod
    def _config(cls, html: str, key: str) -> str | None:
        match = re.search(cls._config_value.format(key=re.escape(key)), html)
        return match.group("value") if match else None

    @classmethod
    def _vacancy(cls, payload: object) -> ExtractedVacancy | None:
        if not isinstance(payload, dict):
            return None
        info = payload.get("jobPostingInfo")
        if not isinstance(info, dict):
            return None
        description = cls._description(info.get("jobDescription"))
        if description is None:
            return None
        organization = payload.get("hiringOrganization")
        company = organization.get("name") if isinstance(organization, dict) else organization
        return ExtractedVacancy(
            title=cls._value(info.get("title")),
            company=cls._value(company),
            location=cls._value(info.get("location")),
            description=description,
            posted_at=cls._date(info.get("startDate")),
            employment_type=cls._value(info.get("timeType")),
            work_arrangement=cls._value(info.get("remoteType")),
        )

    @staticmethod
    def _value(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _description(value: object) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        parser = _WorkdayDescriptionText()
        parser.feed(value)
        parser.close()
        return parser.text()

    @staticmethod
    def _date(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(value), time.min)
            except ValueError:
                return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


class _WorkdayDescriptionText(HTMLParser):
    _block_tags = {"br", "div", "li", "p", "section", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in self._block_tags:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._block_tags:
            self._break()

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            if self._parts and self._parts[-1] != "\n":
                self._parts.append(" ")
            self._parts.append(text)

    def _break(self) -> None:
        if self._parts and self._parts[-1] != "\n":
            self._parts.append("\n")

    def text(self) -> str | None:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text or None
