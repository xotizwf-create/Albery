"""Download links must survive being retyped by the model.

The link's filename is covered by its HMAC, so a single dropped character turned a live
link into a 404: on 19.07.2026 the lawyer agent delivered «…оказания слуг…» for a file
actually named «…оказания услуг…», the user got a broken link, asked again and the second
link worked. Two defences are tested here: short ASCII names in the URL, and rebuilding a
link whose filename no longer matches its signature.
"""
from __future__ import annotations

import re
from urllib.parse import quote


def _make_export(app_module, tmp_path, monkeypatch, display_name, ext="docx"):
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    import b24bot

    monkeypatch.setattr(b24bot, "ZOOM_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(b24bot, "cleanup_zoom_exports", lambda: 0)
    return b24bot._b24_save_export(b"docx-bytes", display_name, ext)


def test_url_carries_short_ascii_name_not_the_russian_title(app_module, tmp_path, monkeypatch):
    long_title = "Шаблон договора оказания услуг по созданию и сопровождению IT-платформы"
    url = _make_export(app_module, tmp_path, monkeypatch, long_title)

    path = url.split("/zoom-export/", 1)[1]
    filename = path.rsplit("/", 1)[-1]
    assert filename.isascii(), "a russian name in the URL is what the model corrupts"
    assert "%" not in filename, "percent-encoding is the corruptible part — keep it out"
    assert len(url) < 160, f"URL still long enough to be mis-copied: {len(url)}"


def test_downloaded_file_keeps_the_human_title(app_module, tmp_path, monkeypatch):
    title = "Досудебная претензия ООО АЛБЕРИ.docx"
    url = _make_export(app_module, tmp_path, monkeypatch, title)
    stored = url.rsplit("/", 1)[-1]

    assert app_module.zoom.export_display_name(stored) == title


def test_unknown_file_falls_back_to_its_own_name(app_module, tmp_path, monkeypatch):
    """Legacy links have no sidecar — the stored name must still be served."""
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    assert app_module.zoom.export_display_name("1784446767_legacy.docx") == "1784446767_legacy.docx"


def test_corrupted_link_is_repaired_to_the_real_file(app_module, tmp_path, monkeypatch):
    """The exact 19.07.2026 failure: one character dropped from the filename."""
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    real_name = "1784446767_Шаблон договора оказания услуг.docx"
    (tmp_path / real_name).write_bytes(b"x")
    expires = 1784448567
    token = app_module.zoom._zoom_export_token(real_name, expires)

    damaged = "1784446767_Шаблон договора оказания слуг.docx"  # «услуг» -> «слуг»
    answer = f"Готово: https://mcp.m4s.ru/zoom-export/{expires}/{token}/{quote(damaged)}"

    fixed = app_module.zoom.repair_export_links(answer)

    m = re.search(r"/zoom-export/(\d+)/([0-9a-f]+)/(\S+)", fixed)
    exp, tok, name = int(m.group(1)), m.group(2), m.group(3)
    assert tok == app_module.zoom._zoom_export_token(app_module.zoom.unquote(name), exp), \
        "repaired link must verify against its own signature"
    assert app_module.zoom.unquote(name) == real_name


def test_intact_link_is_left_untouched(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    name = "1784446767_ab12cd34.docx"
    (tmp_path / name).write_bytes(b"x")
    expires = 1784448567
    token = app_module.zoom._zoom_export_token(name, expires)
    answer = f"https://mcp.m4s.ru/zoom-export/{expires}/{token}/{name}"

    assert app_module.zoom.repair_export_links(answer) == answer


def test_damaged_token_is_re_signed_when_the_file_exists(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    name = "1784446767_ab12cd34.docx"
    (tmp_path / name).write_bytes(b"x")
    answer = f"https://mcp.m4s.ru/zoom-export/1784448567/{'0' * 32}/{name}"

    fixed = app_module.zoom.repair_export_links(answer)

    m = re.search(r"/zoom-export/(\d+)/([0-9a-f]+)/(\S+)", fixed)
    assert m.group(2) == app_module.zoom._zoom_export_token(name, int(m.group(1)))


def test_link_to_a_vanished_file_is_left_alone(app_module, tmp_path, monkeypatch):
    """Nothing to point at — must not invent a link to some other document."""
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    (tmp_path / "1784446767_other.docx").write_bytes(b"x")
    answer = f"https://mcp.m4s.ru/zoom-export/1784448567/{'0' * 32}/1784446767_gone.docx"

    assert app_module.zoom.repair_export_links(answer) == answer


def test_text_without_links_is_unchanged(app_module):
    assert app_module.zoom.repair_export_links("Просто ответ без ссылок") == "Просто ответ без ссылок"


def test_pdf_export_uses_the_same_short_name_scheme(app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    import b24bot

    monkeypatch.setattr(b24bot, "ZOOM_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(b24bot, "cleanup_zoom_exports", lambda: 0)
    url = b24bot._b24_save_pdf_export(b"%PDF-1.4", "Договор оказания услуг _ 26.06.2026")

    stored = url.rsplit("/", 1)[-1]
    assert stored.isascii() and stored.endswith(".pdf")
    assert app_module.zoom.export_display_name(stored) == "Договор оказания услуг _ 26.06.2026.pdf"


def test_agent_sees_the_human_title_of_its_own_document(app_module, tmp_path, monkeypatch):
    """The next turn quotes the last generated document back to the agent; an opaque
    «1784531605_a1b2c3d4.docx» there would make it unable to recognise its own file."""
    import b24bot

    monkeypatch.setattr(app_module.zoom, "ZOOM_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(b24bot, "ZOOM_EXPORT_DIR", tmp_path)
    monkeypatch.setattr(b24bot, "cleanup_zoom_exports", lambda: 0)
    url = b24bot._b24_save_export(b"x", "Договор оказания услуг", "docx")
    stored_name = url.rsplit("/", 1)[-1]

    captured = {}
    monkeypatch.setattr(b24bot, "_b24_extract_document", lambda data, fname: "текст договора")
    import attachments as _att
    monkeypatch.setattr(_att, "store_attachment",
                        lambda **kw: captured.update(kw), raising=False)

    b24bot._b24_capture_generated_doc("16", "agent-sklad", 16, f"Готово: /zoom-export/1/2/{stored_name}")

    assert captured["file_name"] == "Договор оказания услуг.docx"
