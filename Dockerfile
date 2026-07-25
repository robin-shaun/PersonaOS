FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system personaos \
    && useradd --system --gid personaos --home-dir /app personaos

COPY --chown=personaos:personaos . /app

RUN python -m pip install --no-cache-dir .

RUN mkdir -p /app/var \
    && chown -R personaos:personaos /app/var

USER personaos

EXPOSE 18110

CMD ["python", "-m", "apps.api"]
