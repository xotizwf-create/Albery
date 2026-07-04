"""Loader for the GitHub-backed knowledge registry (agent_knowledge.py)."""
import importlib

import agent_knowledge


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
