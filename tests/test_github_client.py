from __future__ import annotations

from adapters.github.client import proxy_url_from_environment


def test_nonstandard_socks_proxy_is_normalized(monkeypatch) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "socks://127.0.0.1:7897")
    monkeypatch.delenv("https_proxy", raising=False)

    assert (
        proxy_url_from_environment("https://api.github.com")
        == "socks5://127.0.0.1:7897"
    )

