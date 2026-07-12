"""Registry filenames must stay checkout-able on Windows: a colon in a self-learned instruction
name («Новостные сводки: искать последнюю») aborted every Windows clone with 'invalid path'."""
from __future__ import annotations

import agent_knowledge as ak


def test_windows_forbidden_chars_are_mapped():
    out = ak._safe_component('Сводки: план? "важно" <тут> a/b\\c|d*e')
    for bad in '/\\:*?"<>|':
        assert bad not in out, f"{bad!r} must not survive in a filename"
    assert "Сводки" in out and "план" in out  # remains readable


def test_learned_file_path_is_safe():
    p = ak._learned_file("main", "Новостные сводки: искать последнюю")
    assert ":" not in p.name
    assert p.name.endswith(".md")


def test_empty_name_falls_back():
    assert ak._safe_component("") == "unnamed"
    assert ak._safe_component("...") == "unnamed"
