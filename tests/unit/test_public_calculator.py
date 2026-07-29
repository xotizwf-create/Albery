from __future__ import annotations

from urllib.parse import unquote


def test_calculator_is_public_but_similar_paths_stay_protected(
    app_module, client, monkeypatch, tmp_path
):
    calculator_dist = tmp_path / "calculator-dist"
    assets = calculator_dist / "assets"
    assets.mkdir(parents=True)
    (calculator_dist / "index.html").write_text(
        "<!doctype html><title>Калькулятор ИУ</title>", encoding="utf-8"
    )
    (assets / "app.js").write_text("console.log('calculator')", encoding="utf-8")
    monkeypatch.setattr(app_module, "CALCULATOR_DIST", calculator_dist)

    page = client.get("/Калькулятор/")
    asset = client.get("/Калькулятор/assets/app.js")
    protected = client.get("/Калькулятор-черновик")

    assert page.status_code == 200
    assert "Калькулятор ИУ" in page.get_data(as_text=True)
    assert asset.status_code == 200
    assert "calculator" in asset.get_data(as_text=True)
    assert protected.status_code == 302
    assert "/login" in protected.headers["Location"]


def test_calculator_url_without_slash_has_a_canonical_public_redirect(client):
    response = client.get("/Калькулятор")

    assert response.status_code == 308
    assert unquote(response.headers["Location"]).endswith("/Калькулятор/")


def test_auth_exemptions_require_a_real_route_boundary(app_module):
    assert app_module.auth_exempt_path("/login") is True
    assert app_module.auth_exempt_path("/login/anything") is False
    assert app_module.auth_exempt_path("/mcp") is True
    assert app_module.auth_exempt_path("/mcp/messages/abc") is True
    assert app_module.auth_exempt_path("/mcp-malicious") is False
    assert app_module.auth_exempt_path("/Калькулятор/assets/app.js") is True
    assert app_module.auth_exempt_path("/Калькулятор-черновик") is False


def test_missing_flask_secret_uses_an_unpredictable_ephemeral_key(
    app_module, monkeypatch
):
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)

    first = app_module.session_signing_secret()
    second = app_module.session_signing_secret()

    assert first != app_module._INSECURE_SESSION_SECRET
    assert len(first) >= 48
    assert first != second
