from app.agents.job_extraction import JobExtractor
from app.schemas.job import JobProfile


class JobAnalysisService:
    def __init__(self, extractor: JobExtractor) -> None:
        self._extractor = extractor

    def analyse_text(self, job_text: str) -> JobProfile:
        return self._extractor.extract(job_text)
