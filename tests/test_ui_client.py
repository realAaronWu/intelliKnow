"""TLS selection for the Streamlit API client."""

from app.ui.client import _verify_for_url


def test_http_api_ignores_configured_ca_file(monkeypatch):
    monkeypatch.setenv("INTELLIKNOW_CA_CERT", "/missing/rootCA.pem")

    assert _verify_for_url("http://127.0.0.1:8012") is True


def test_https_api_uses_configured_ca_file(monkeypatch):
    monkeypatch.setenv("INTELLIKNOW_CA_CERT", "/trusted/rootCA.pem")

    assert _verify_for_url("https://127.0.0.1:8012") == "/trusted/rootCA.pem"
