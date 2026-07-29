"""Правка СУЩЕСТВУЮЩЕГО Google-документа — возможность, которой у агента не было.

Жалоба владельца 29.07.2026 (dialog 22, ходы 1320–1323): «сегодня не редактирует гугл
документы, хотя у него были все доступы раньше». Разбор показал, что доступ был всегда
(a9ent.ai@gmail.com — владелец этих документов, canEdit=true), а инструмента правки не
существовало никогда: в реестре был только create_google_doc. Агент выдал отсутствие
инструмента за отсутствие прав («доступ выдаёт Александр Никитенко») и пообещал передать
запрос — то есть отчитался о действии, которого не было.

Эти тесты фиксируют: инструменты чтения и правки существуют, правка идёт В ТОТ ЖЕ документ
(id и ссылка не меняются), результат перечитывается, а отказ бывает только предметным.
"""
from __future__ import annotations

import pytest

DOC_MIME = "application/vnd.google-apps.document"
DOC_ID = "1iEEy2xN88iwk9EePVGpmTsHJE_bC4tCi-pi2L65cyms"  # «Ответы на частые вопросы» с прода


@pytest.fixture(scope="module")
def app_mod():
    import app

    return app


class FakeDriveFiles:
    def __init__(self, drive):
        self.drive = drive

    def get(self, fileId=None, fields=None, **kw):  # noqa: N803
        return _Exec(dict(self.drive.meta))

    def export(self, fileId=None, mimeType=None, **kw):  # noqa: N803
        if mimeType == "text/html":
            return _Exec(self.drive.html.encode("utf-8"))
        return _Exec(self.drive.text.encode("utf-8"))

    def update(self, fileId=None, media_body=None, body=None, fields=None, **kw):  # noqa: N803
        self.drive.updated_with = media_body
        self.drive.text = "ЗАПИСАНО"
        self.drive.html = "<p>ЗАПИСАНО</p>"
        return _Exec({**self.drive.meta, "modifiedTime": "2026-07-29T19:00:00Z"})


class FakeDrive:
    def __init__(self, mime: str = DOC_MIME, can_edit: bool = True):
        self.meta = {
            "id": DOC_ID,
            "name": "Ответы на частые вопросы",
            "mimeType": mime,
            "webViewLink": f"https://docs.google.com/document/d/{DOC_ID}/edit",
            "modifiedTime": "2026-07-28T10:00:00Z",
            "capabilities": {"canEdit": can_edit},
        }
        self.text = "В: Какая комиссия?\nО: 44%."
        self.html = "<p>В: Какая комиссия?</p><p>О: 44%.</p>"
        self.updated_with = None

    def files(self):
        return FakeDriveFiles(self)

    def revisions(self):
        return self

    def list(self, fileId=None, fields=None, **kw):  # noqa: N803
        return _Exec({"revisions": [{"id": "1", "modifiedTime": "2026-07-28T10:00:00Z"}]})


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


@pytest.fixture
def drive(app_mod, monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(app_mod, "_google_user_credentials", lambda: object())
    monkeypatch.setattr(app_mod, "_build_drive_service", lambda creds=None: fake, raising=False)
    # google-api-python-client не ставится в окружение прогона тестов — подменяем только обёртку
    monkeypatch.setattr(app_mod, "_drive_docx_media", lambda data: ("docx", len(data)), raising=False)
    return fake


class TestToolsExist:
    def test_read_and_edit_tools_are_registered_for_employees(self, ctx):
        assert "read_google_doc" in ctx.TOOLS
        assert "edit_google_doc" in ctx.TOOLS
        # сотрудники работают на ops-уровне — там инструменты и понадобились
        assert {"read_google_doc", "edit_google_doc"} <= set(ctx.OPS_TOOL_NAMES)

    def test_google_toolset_is_symmetric_in_the_core_set(self, ctx):
        """Возможность, которой агент не видит, — это возможность, которой у него нет.

        create_google_doc лежал в CORE (модель видит его каждый ход), а чтения и правки не было
        нигде — поэтому агент и решил, что дело в правах доступа. Для таблиц набор был полным.
        """
        for entity, names in (
            ("документы", {"create_google_doc", "read_google_doc", "edit_google_doc"}),
            ("таблицы", {"create_google_sheet", "read_google_sheet_values", "write_google_sheet_values"}),
        ):
            missing = names - set(ctx.CORE_TOOL_NAMES)
            assert not missing, f"{entity}: модель не видит {sorted(missing)}"

    def test_edit_requires_confirm(self, ctx):
        with pytest.raises(ctx.McpError):
            ctx.TOOLS["edit_google_doc"]["handler"]({"document_id": DOC_ID, "html": "<p>x</p>"})


class TestReadGoogleDoc:
    def test_reads_html_for_editing(self, app_mod, drive):
        res = app_mod.read_google_doc(f"https://docs.google.com/document/d/{DOC_ID}/edit?tab=t.0", "html")
        assert res["document_id"] == DOC_ID
        assert res["format"] == "html"
        assert "Какая комиссия" in res["content"]
        assert res["can_edit"] is True

    def test_rejects_non_document(self, app_mod, monkeypatch):
        fake = FakeDrive(mime="application/vnd.google-apps.spreadsheet")
        monkeypatch.setattr(app_mod, "_google_user_credentials", lambda: object())
        monkeypatch.setattr(app_mod, "_build_drive_service", lambda creds=None: fake, raising=False)
        with pytest.raises(RuntimeError, match="не Google-документ"):
            app_mod.read_google_doc(DOC_ID)


class TestEditGoogleDoc:
    def test_edits_in_place_and_reads_back(self, app_mod, drive):
        res = app_mod.edit_google_doc(DOC_ID, "<p>В: <b>Какая комиссия?</b></p><p>О: 44%.</p>")
        assert res["document_id"] == DOC_ID  # тот же документ
        assert res["url"].endswith(f"/document/d/{DOC_ID}/edit")  # та же ссылка
        assert drive.updated_with is not None  # правка реально ушла в Drive
        assert res["previous_revision_id"] == "1"  # есть куда откатиться
        assert res["content_length"] > 0 and res["content_preview"]  # перечитано после записи

    def test_no_silent_success_on_empty_html(self, app_mod, drive):
        with pytest.raises(RuntimeError, match="пуст"):
            app_mod.edit_google_doc(DOC_ID, "   ")

    def test_missing_rights_say_exactly_that(self, app_mod, monkeypatch):
        fake = FakeDrive(can_edit=False)
        monkeypatch.setattr(app_mod, "_google_user_credentials", lambda: object())
        monkeypatch.setattr(app_mod, "_build_drive_service", lambda creds=None: fake, raising=False)
        with pytest.raises(RuntimeError, match="a9ent.ai@gmail.com"):
            app_mod.edit_google_doc(DOC_ID, "<p>текст</p>")
