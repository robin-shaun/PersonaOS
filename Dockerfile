FROM python:3.14.6-slim-bookworm@sha256:86f975aca15cf04a40b399eebede9aea7c82eae084d1f1a0a6ef6bcaae871a30

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system personaos \
    && useradd --system --gid personaos --home-dir /app personaos

COPY --chown=personaos:personaos . /app

RUN python -m pip install --no-cache-dir --index-url https://pypi.org/simple --require-hashes -r requirements.lock \
    && python -m pip install --no-cache-dir \
        --index-url https://pypi.org/simple \
        --no-deps \
        --no-build-isolation \
        .

RUN mkdir -p /app/var \
    && chown -R personaos:personaos /app/var

USER personaos

EXPOSE 18110

CMD ["python", "-m", "apps.api"]
