ARG PYTHON_BASE_IMAGE=python:3.12-slim@sha256:46cb7cc2877e60fbd5e21a9ae6115c30ace7a077b9f8772da879e4590c18c2e3
FROM ${PYTHON_BASE_IMAGE}

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

WORKDIR /app/backend
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=60
ENV PYTHONPATH=/app/backend:/app/backend/packages/harness

RUN apt-get update \
    && apt-get install -y --fix-missing --no-install-recommends git tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# Keep dependency installation independent from source changes so code-only
# Docker refreshes do not rerun pip install.
COPY pyproject.toml /app/backend/pyproject.toml
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then python -m pip config set global.index-url "$PIP_INDEX_URL"; fi; \
    if [ -n "$PIP_TRUSTED_HOST" ]; then python -m pip config set global.trusted-host "$PIP_TRUSTED_HOST"; fi; \
    python -c 'import pathlib, tomllib; data = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8")); reqs = ["setuptools>=69", "wheel"]; reqs.extend(data["project"].get("dependencies", [])); reqs.extend(data["project"].get("optional-dependencies", {}).get("observability", [])); pathlib.Path("/tmp/anvil-runtime-requirements.txt").write_text("\n".join(reqs) + "\n", encoding="utf-8")'; \
    python -m pip install --no-build-isolation --retries 10 --progress-bar off --prefer-binary -r /tmp/anvil-runtime-requirements.txt

COPY . /app/backend

EXPOSE 18000

CMD ["python", "-m", "app.gateway.main", "--host", "0.0.0.0", "--port", "18000"]
