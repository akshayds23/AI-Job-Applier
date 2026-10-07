# Backend image: FastAPI + Tectonic (LaTeX) for resume PDFs.
# Build from the project root:   docker build -f backend/Dockerfile -t ai-applier-backend .
# Run:                           docker run -p 8000:8000 --env-file .env -v applier-data:/app/data ai-applier-backend
FROM python:3.12-slim

ARG TECTONIC_VERSION=0.17.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ASSISTED_APPLY_ENABLED=false

# ca-certificates: Tectonic downloads LaTeX packages over HTTPS.
# fonts-liberation/dejavu: Unicode TrueType fonts for the built-in (ATS-safe) PDF renderer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Tectonic: a single static binary (musl build has no system library dependencies).
RUN curl -fsSL "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin tectonic \
    && tectonic --version

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user; Tectonic's package cache lives in its home directory.
RUN useradd --create-home --uid 1000 app
COPY --chown=app:app backend/ /app/backend/
RUN mkdir -p /app/data /app/sessions /app/templates && chown -R app:app /app
USER app

# Pre-fill the LaTeX package cache so the first resume compiles in seconds, not minutes.
RUN python -m services.latex_compiler

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
