FROM python:3.14.7-slim-bookworm@sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52

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
