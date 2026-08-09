FROM python:3.14.0-slim-bookworm@sha256:d13fa0424035d290decef3d575cea23d1b7d5952cdf429df8f5542c71e961576

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
