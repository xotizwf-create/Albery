"""Loader for the GitHub-backed knowledge registry (agent_knowledge.py)."""
import importlib
import os

import pytest

import agent_knowledge


@pytest.fixture(autouse=True)
def _knowledge_module_restored():
    """Вернуть модуль на настоящий реестр после теста.

    Тесты перенацеливают его на tmp через reload; monkeypatch откатывает переменную окружения,
    но НЕ глобальные пути внутри уже перезагруженного модуля. Из-за этого следующие тесты
    видели пустой реестр — так «потерялись» подключённые инструкции агента (22.07.2026)."""
    yield
    os.environ.pop("AGENT_KNOWLEDGE_DIR", None)
    importlib.reload(agent_knowledge)


def _reload_with(root, monkeypatch):
    monkeypatch.setenv("AGENT_KNOWLEDGE_DIR", str(root))
    return importlib.reload(agent_knowledge)


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_absent_registry_returns_none(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "nope", monkeypatch)
    assert k.registry_present() is False
    assert k.load_instructions() is None
    assert k.load_skills() is None


def test_instruction_paths_scope_and_nesting(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    _write(reg, "instructions/Базовое поведение.md",
           "---\nname: Базовое поведение\nscope: universal\nsort_order: 0\n---\n\nТело.\n")
    _write(reg, "instructions/Базовое поведение/Вопросы о доступе.md",
           "---\nname: Вопросы о доступе\nscope: optional\n---\n\nДетали.\n")
    _write(reg, "instructions/Формирование отчетов/Еженедельный отчет.md",
           "---\nname: Еженедельный отчет\nscope: optional\n---\n\nШаблон\nмного строк.\n")
    k = _reload_with(reg, monkeypatch)

    items = {i["id"]: i for i in k.load_instructions()}
    # Path id matches the DB " / " join used by context_server.load_ai_instructions.
    assert "Базовое поведение" in items
    assert "Базовое поведение / Вопросы о доступе" in items
    assert "Формирование отчетов / Еженедельный отчет" in items

    nested = items["Базовое поведение / Вопросы о доступе"]
    assert nested["parent"] == "Базовое поведение"
    assert nested["scope"] == "optional"
    # multi-line body is preserved (joined), not collapsed
    assert "много строк" in items["Формирование отчетов / Еженедельный отчет"]["content"]

    assert k.universal_instruction_paths() == {"Базовое поведение"}


def test_skills_shared_vs_hermes_base(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    _write(reg, "skills/tg-access/SKILL.md",
           "---\nname: tg-access\ndescription: Управление доступом Telegram.\n---\n\nбоди\n")
    _write(reg, "hermes_base/apple/foo/SKILL.md",
           "---\nname: foo\ndescription: apple foo skill.\n---\n\nбоди\n")
    k = _reload_with(reg, monkeypatch)

    skills = {s["id"]: s for s in k.load_skills()}
    assert skills["skill:tg-access"]["kind"] == "shared"
    assert skills["skill:tg-access"]["custom"] is True
    assert skills["skill:apple/foo"]["kind"] == "hermes_base"
    assert skills["skill:apple/foo"]["parent"] == "apple"
    assert skills["skill:apple/foo"]["custom"] is False


def test_scope_defaults_to_universal_on_bad_value(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    _write(reg, "instructions/X.md", "---\nname: X\nscope: garbage\n---\n\nтело\n")
    _write(reg, "instructions/Y.md", "---\nname: Y\n---\n\nтело\n")
    k = _reload_with(reg, monkeypatch)
    items = {i["id"]: i for i in k.load_instructions()}
    assert items["X"]["scope"] == "universal"
    assert items["Y"]["scope"] == "universal"


def _base_registry(reg):
    _write(reg, "instructions/Базовое.md", "---\nname: Базовое\nscope: universal\n---\n\nвсем\n")
    _write(reg, "instructions/Отдел/Закупки.md", "---\nname: Закупки\nscope: optional\n---\n\nтолько закупкам\n")
    _write(reg, "instructions/Отдел/Продажи.md", "---\nname: Продажи\nscope: optional\n---\n\nтолько продажам\n")


def test_manifest_roundtrip(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    _base_registry(reg)
    k = _reload_with(reg, monkeypatch)
    assert k.load_manifest("sales") == {"instructions": [], "skills": []}
    k.save_manifest("sales", ["Отдел / Продажи"], ["skill:tg-access"])
    got = k.load_manifest("sales")
    assert got["instructions"] == ["Отдел / Продажи"]
    assert got["skills"] == ["skill:tg-access"]


def test_allowed_instruction_paths_is_universal_plus_connected(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    _base_registry(reg)
    k = _reload_with(reg, monkeypatch)
    # No manifest yet -> only universal.
    assert k.allowed_instruction_paths("sales") == {"Базовое"}
    # Connect one optional instruction -> universal + that one, never the other optional.
    k.save_manifest("sales", ["Отдел / Продажи"], [])
    assert k.allowed_instruction_paths("sales") == {"Базовое", "Отдел / Продажи"}
    assert "Отдел / Закупки" not in k.allowed_instruction_paths("sales")


def test_allowed_paths_none_when_registry_absent(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "nope", monkeypatch)
    assert k.allowed_instruction_paths("sales") is None


def test_set_instruction_scope_flips_frontmatter(tmp_path, monkeypatch):
    reg = tmp_path / "reg"
    _base_registry(reg)
    k = _reload_with(reg, monkeypatch)
    assert k.set_instruction_scope("Базовое", "optional") is True
    items = {i["id"]: i for i in k.load_instructions()}
    assert items["Базовое"]["scope"] == "optional"
    assert items["Базовое"]["content"].strip() == "всем"  # body preserved
    assert k.set_instruction_scope("Нет такой", "optional") is False


def test_context_server_scopes_instructions(tmp_path, monkeypatch):
    """load_ai_instructions(allowed_paths=…) returns only the scoped subset; None = all."""
    reg = tmp_path / "reg"
    _base_registry(reg)
    _reload_with(reg, monkeypatch)
    import mcp.context_server as cs

    all_paths = {r["path"] for r in cs.load_ai_instructions()}
    assert {"Базовое", "Отдел / Закупки", "Отдел / Продажи"} <= all_paths

    scoped = {r["path"] for r in cs.load_ai_instructions(allowed_paths={"Базовое", "Отдел / Продажи"})}
    assert scoped == {"Базовое", "Отдел / Продажи"}
    assert "Отдел / Закупки" not in scoped


def test_learned_absent_returns_none(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "reg", monkeypatch)
    assert k.load_agent_learned("sales") is None


def test_learned_save_load_attribution(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "reg", monkeypatch)
    k.save_agent_learned("sales", "Формат отчёта", "Всегда таблицей.", source="self",
                         actor="агент «Продажник» (самообучение)", dialog="chat42")
    items = k.load_agent_learned("sales")
    assert len(items) == 1
    it = items[0]
    assert it["name"] == "Формат отчёта"
    assert it["source"] == "self"
    assert it["created_by"] == "агент «Продажник» (самообучение)"
    assert it["origin_dialog"] == "chat42"
    assert it["created_at"] and it["updated_at"]
    assert k.count_agent_self_learned("sales") == 1


def test_learned_update_preserves_creator_stamps_editor(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "reg", monkeypatch)
    k.save_agent_learned("sales", "Правило", "v1", source="self", actor="агент X")
    first = k.load_agent_learned("sales")[0]
    k.save_agent_learned("sales", "Правило", "v2", source="owner", actor="владелец")
    second = k.load_agent_learned("sales")[0]
    assert second["content"] == "v2"
    assert second["created_by"] == "агент X"          # creator preserved
    assert second["created_at"] == first["created_at"]
    assert second["updated_by"] == "владелец"          # editor updated


def test_learned_delete_only_self_protects_owner(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "reg", monkeypatch)
    k.save_agent_learned("sales", "Владельца", "x", source="owner", actor="владелец")
    assert k.delete_agent_learned("sales", "Владельца", only_self=True) is False  # protected
    assert k.delete_agent_learned("sales", "Владельца", only_self=False) is True  # owner can
    k.save_agent_learned("sales", "Своё", "y", source="self", actor="агент")
    assert k.delete_agent_learned("sales", "Своё", only_self=True) is True


def test_promote_learned_to_library_optional(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "reg", monkeypatch)
    k.save_agent_learned("sales", "Формат остатков", "Таблицей по складам.", source="self", actor="агент X")
    res = k.promote_learned_to_library("sales", "Формат остатков")
    assert res and res["name"] == "Формат остатков"
    lib = {i["path"]: i for i in k.load_instructions()}
    promoted = lib.get(res["path"])
    assert promoted is not None
    assert promoted["scope"] == "optional"                 # connectable per-agent, real teeth
    assert "Таблицей по складам" in promoted["content"]
    # the personal copy is untouched
    assert any(i["name"] == "Формат остатков" for i in k.load_agent_learned("sales"))
    # missing source -> None
    assert k.promote_learned_to_library("sales", "нет такой") is None


def test_resync_preserves_scope_and_removes_stale(tmp_path, monkeypatch):
    k = _reload_with(tmp_path / "reg", monkeypatch)
    rows = [
        {"path": "Базовое", "name": "Базовое", "content": "всем", "sort_order": 0, "id": "u1"},
        {"path": "Отдел / Закупки", "name": "Закупки", "content": "закуп", "sort_order": 1, "id": "u2"},
    ]
    written, removed = k.resync_instructions_to_git(rows)
    assert (written, removed) == (2, 0)
    assert {i["id"]: i for i in k.load_instructions()}["Базовое"]["scope"] == "universal"

    # owner marks one optional; a later resync with UNCHANGED content must preserve it
    k.set_instruction_scope("Базовое", "optional")
    k.resync_instructions_to_git(rows)
    assert {i["id"]: i for i in k.load_instructions()}["Базовое"]["scope"] == "optional"

    # a promoted library file (no db_id) must survive a resync
    promo_dir = k.INSTRUCTIONS_DIR / "Навыки агентов"
    promo_dir.mkdir(parents=True, exist_ok=True)
    (promo_dir / "Промо.md").write_text(
        "---\nname: Промо\nscope: optional\npromoted_from: sales\n---\n\nтело\n", encoding="utf-8")

    # dropping a DB row deletes ITS file (has db_id) but keeps the promoted one
    k.resync_instructions_to_git([rows[0]])
    paths = {i["path"] for i in k.load_instructions()}
    assert "Отдел / Закупки" not in paths        # DB-sourced -> removed
    assert "Навыки агентов / Промо" in paths       # promoted -> preserved
    assert "Базовое" in paths
