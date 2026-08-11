from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "apps" / "web"


def test_web_dependencies_are_locked_and_build_is_reproducible() -> None:
    package = json.loads((WEB_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads(
        (WEB_ROOT / "package-lock.json").read_text(encoding="utf-8")
    )

    assert package["version"] == "0.12.0"
    assert package["engines"]["node"] == ">=22.12"
    assert package["scripts"]["build"] == "tsc -b && vite build"
    assert package["scripts"]["test"] == "vitest run"
    assert package["dependencies"] == {
        "react": "19.2.8",
        "react-dom": "19.2.8",
    }
    assert lock["lockfileVersion"] == 3
    assert lock["packages"][""]["version"] == package["version"]
    assert lock["packages"][""]["dependencies"] == package["dependencies"]


def test_web_container_is_unprivileged_and_proxies_same_origin_api() -> None:
    dockerfile = (WEB_ROOT / "Dockerfile").read_text(encoding="utf-8")
    nginx = (WEB_ROOT / "nginx.conf").read_text(encoding="utf-8")

    assert "nginxinc/nginx-unprivileged:" in dockerfile
    assert dockerfile.count("@sha256:") == 2
    assert "npm ci" in dockerfile
    assert "COPY --from=build --chown=101:101" in dockerfile
    assert "listen 8080;" in nginx
    assert "location /api/" in nginx
    assert "proxy_pass http://api:18110;" in nginx
    assert "location = /healthz" in nginx
    assert "frame-ancestors 'none'" in nginx
    assert "frame-src https://challenges.cloudflare.com" in nginx
    assert "script-src 'self' https://challenges.cloudflare.com" in nginx
    assert "map $http_x_forwarded_proto $persona_forwarded_proto" in nginx
    assert nginx.count(
        "proxy_set_header X-Forwarded-Proto $persona_forwarded_proto;"
    ) == 2
    assert "client_max_body_size 6m;" in nginx


def test_smoke_uses_web_origin_and_exercises_the_evidence_loop() -> None:
    smoke = (PROJECT_ROOT / "examples" / "compose_smoke.py").read_text(
        encoding="utf-8"
    )

    assert 'default="http://127.0.0.1:18111"' in smoke
    assert 'client.text("/")' in smoke
    assert 'client.json("GET", "/health"' in smoke
    assert "/memory-candidates/" in smoke
    assert "/conversations/" in smoke
    assert "answer[\"citations\"]" in smoke
    assert "audit trail is incomplete" in smoke
