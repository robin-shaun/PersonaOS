FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

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
