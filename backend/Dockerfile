FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /backend
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .
COPY backend/app ./app
# Application prompt loaders resolve this repository-level directory.
COPY prompts /prompts

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
