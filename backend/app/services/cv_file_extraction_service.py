import hashlib
import io
import json
import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.schemas.cv_ingestion import CVDocumentProvenance, ExtractedCVDocument, ExtractedCVSegment


class CVFileExtractionError(ValueError):
    pass


class CVFileExtractionService:
    _allowed = {".pdf", ".docx", ".md", ".markdown", ".json"}
    _max_bytes = 5 * 1024 * 1024
    _max_files = 5
    _max_batch_bytes = 15 * 1024 * 1024
    _media_types = {
        ".pdf": {"application/pdf", "application/x-pdf", "application/octet-stream"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"},
        ".md": {"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream"},
        ".markdown": {"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream"},
        ".json": {"application/json", "text/json", "application/octet-stream"},
    }

    def validate_batch(self, files: list[tuple[str, str | None, bytes]]) -> None:
        if not files:
            raise CVFileExtractionError("At least one CV file is required.")
        if len(files) > self._max_files:
            raise CVFileExtractionError(f"A CV upload can contain at most {self._max_files} files.")
        if sum(len(content) for _, _, content in files) > self._max_batch_bytes:
            raise CVFileExtractionError("Total CV upload size exceeds the 15 MB batch limit.")

    def extract(self, *, filename: str, content_type: str | None, content: bytes) -> ExtractedCVDocument:
        suffix = Path(filename).suffix.casefold()
        if suffix not in self._allowed:
            raise CVFileExtractionError("Unsupported CV file type. Use PDF, DOCX, Markdown, or JSON.")
        media_type = (content_type or "application/octet-stream").split(";", 1)[0].strip().casefold()
        if media_type not in self._media_types[suffix]:
            raise CVFileExtractionError(f"Unsupported media type for {suffix} CV upload.")
        if not content or len(content) > self._max_bytes:
            raise CVFileExtractionError("CV file is empty or exceeds the 5 MB limit.")
        digest = hashlib.sha256(content).hexdigest()
        if suffix == ".pdf":
            try:
                texts = [(page.extract_text() or "", None) for page in PdfReader(io.BytesIO(content)).pages]
            except Exception as exc:
                raise CVFileExtractionError("PDF could not be read as a text CV.") from exc
        elif suffix == ".docx":
            try:
                texts = self._docx_sections(Document(io.BytesIO(content)))
            except Exception as exc:
                raise CVFileExtractionError("DOCX could not be read as a CV document.") from exc
        else:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CVFileExtractionError("CV text files must be UTF-8 encoded.") from exc
            texts = self._markdown_sections(text) if suffix in {".md", ".markdown"} else [(text, None)]
        segments = []
        for index, (text, heading) in enumerate(texts):
            if text.strip():
                segments.append(
                    ExtractedCVSegment(
                        segment_id=f"{digest}:{index + 1}",
                        text=text.strip(),
                        page_number=index + 1 if suffix == ".pdf" else None,
                        heading=heading,
                    )
                )
        if not segments:
            raise CVFileExtractionError("No extractable text was found in the CV file.")
        return ExtractedCVDocument(
            provenance=CVDocumentProvenance(filename=filename, media_type=content_type or "application/octet-stream", document_sha256=digest, segment_ids=[segment.segment_id for segment in segments]),
            segments=segments,
        )

    @staticmethod
    def _docx_sections(document: Document) -> list[tuple[str, str | None]]:
        sections: list[tuple[str, str | None]] = []
        heading: str | None = None
        lines: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            if paragraph.style.name.casefold().startswith("heading"):
                if lines:
                    sections.append(("\n".join(lines), heading))
                heading, lines = text, [text]
            else:
                lines.append(text)
        if lines:
            sections.append(("\n".join(lines), heading))
        return sections

    @staticmethod
    def _markdown_sections(text: str) -> list[tuple[str, str | None]]:
        sections: list[tuple[str, str | None]] = []
        heading: str | None = None
        lines: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if match:
                if lines:
                    sections.append(("\n".join(lines), heading))
                heading, lines = match.group(1), [line]
            else:
                lines.append(line)
        if lines:
            sections.append(("\n".join(lines), heading))
        return sections

    @staticmethod
    def parsed_json(document: ExtractedCVDocument) -> dict[str, object] | None:
        if not document.provenance.filename.casefold().endswith(".json"):
            return None
        try:
            value = json.loads("\n".join(segment.text for segment in document.segments))
        except json.JSONDecodeError as exc:
            raise CVFileExtractionError("CV JSON must be valid JSON.") from exc
        if not isinstance(value, dict):
            raise CVFileExtractionError("CV JSON must be an object matching the candidate CV schema.")
        return value
