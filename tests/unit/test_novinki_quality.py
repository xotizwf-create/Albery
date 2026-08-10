from __future__ import annotations

import pytest


def _candidate(number):
    return (10, f"file-{number}.txt", "10.08.2026", "author", f"совет номер {number}")


def test_novinki_uses_codex_batches_and_final_pass(monkeypatch):
    import novinki_watch as nw
    import quality_llm

    monkeypatch.setattr(nw, "CODEX_BATCH", 2)
    calls = []

    def fake_quality(prompt, *, purpose, **kwargs):
        calls.append(purpose)
        if purpose == "novinki_batch":
            return {"recommendations": [{
                "category": "WB",
                "recommendation": f"рекомендация {len(calls)}",
                "rationale": "есть факт",
                "source": "file",
                "keywords": ["WB"],
            }]}
        return {"recommendations": [{
            "category": "WB",
            "recommendation": "финальная рекомендация",
            "rationale": "проверено",
            "source": "file",
            "keywords": ["WB"],
        }]}

    monkeypatch.setattr(quality_llm, "run_quality_json", fake_quality)
    result = nw._synthesize([_candidate(1), _candidate(2), _candidate(3)])

    assert calls == ["novinki_batch", "novinki_batch", "novinki_final"]
    assert [r["recommendation"] for r in result] == ["финальная рекомендация"]


def test_novinki_analysis_failure_is_not_treated_as_empty_success(monkeypatch):
    import novinki_watch as nw
    import quality_llm

    monkeypatch.setattr(
        quality_llm,
        "run_quality_json",
        lambda *a, **k: (_ for _ in ()).throw(quality_llm.QualityLLMError("down")),
    )
    with pytest.raises(quality_llm.QualityLLMError):
        nw._synthesize([_candidate(1)])


def test_novinki_hierarchical_merge_considers_every_recommendation(monkeypatch):
    import novinki_watch as nw
    import quality_llm

    monkeypatch.setattr(nw, "CODEX_BATCH", 1)
    monkeypatch.setattr(nw, "CODEX_FINAL_CAP", 2)
    final_inputs = []

    def fake_quality(prompt, *, purpose, **kwargs):
        if purpose == "novinki_batch":
            number = prompt.rsplit("номер ", 1)[-1]
            return {"recommendations": [{"category": "WB", "recommendation": f"r{number}"}]}
        final_inputs.append(prompt)
        return {"recommendations": [{"category": "WB", "recommendation": f"merged-{len(final_inputs)}"}]}

    monkeypatch.setattr(quality_llm, "run_quality_json", fake_quality)
    result = nw._synthesize([_candidate(i) for i in range(5)])

    assert len(final_inputs) == 6  # 5→3→2 without dropping an input, then final editorial pass
    assert result[0]["recommendation"] == "merged-6"


@pytest.mark.parametrize("bad", [
    {},
    {"recommendations": "not-a-list"},
    {"recommendations": [{"category": "WB"}]},
    {"recommendations": ["not-an-object"]},
])
def test_novinki_malformed_schema_fails_closed(monkeypatch, bad):
    import novinki_watch as nw
    import quality_llm

    monkeypatch.setattr(quality_llm, "run_quality_json", lambda *a, **k: bad)
    with pytest.raises(RuntimeError, match="schema"):
        nw._synthesize([_candidate(1)])


def test_novinki_merge_may_converge_on_fourth_round(monkeypatch):
    import novinki_watch as nw
    import quality_llm

    monkeypatch.setattr(nw, "CODEX_BATCH", 1)
    monkeypatch.setattr(nw, "CODEX_FINAL_CAP", 2)
    final_calls = []

    def fake_quality(prompt, *, purpose, **kwargs):
        if purpose == "novinki_batch":
            number = prompt.rsplit("номер ", 1)[-1]
            return {"recommendations": [{"category": "WB", "recommendation": f"r{number}"}]}
        final_calls.append(prompt)
        return {"recommendations": [{"category": "WB", "recommendation": f"m{len(final_calls)}"}]}

    monkeypatch.setattr(quality_llm, "run_quality_json", fake_quality)
    result = nw._synthesize([_candidate(i) for i in range(17)])

    assert len(final_calls) == 20  # 17→9→5→3→2, then one final pass
    assert result[0]["recommendation"] == "m20"


def test_novinki_main_keeps_source_files_when_synthesis_fails(monkeypatch):
    """A provider/schema failure must abort before any source-file cleanup."""
    import sys
    import types
    __import__("app")  # main() deliberately imports the initialized runtime
    import novinki_watch as nw

    class Request:
        @staticmethod
        def execute():
            return {"files": [{"id": "f1", "name": "one.txt", "mimeType": "text/plain"}]}

    class Files:
        @staticmethod
        def list(**kwargs):
            return Request()

    class Drive:
        @staticmethod
        def files():
            return Files()

    drive = Drive()
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = lambda name, *a, **k: drive if name == "drive" else object()
    googleapiclient = types.ModuleType("googleapiclient")
    googleapiclient.discovery = discovery
    fake_gdrive = types.ModuleType("gdrive")
    fake_gdrive._google_user_credentials = lambda: object()
    monkeypatch.setitem(sys.modules, "googleapiclient", googleapiclient)
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", discovery)
    monkeypatch.setitem(sys.modules, "gdrive", fake_gdrive)
    monkeypatch.setattr(nw, "_extract_text", lambda *a, **k: ("text", "useful source text"))
    monkeypatch.setattr(nw, "_score_candidates", lambda *a, **k: [_candidate(1)])
    monkeypatch.setattr(nw, "_synthesize", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("schema failure")))
    monkeypatch.setattr(
        nw,
        "_delete_items",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("source files must be retained")),
    )
    monkeypatch.setattr(sys, "argv", ["novinki_watch.py", "--dry-run"])

    with pytest.raises(RuntimeError, match="schema failure"):
        nw.main()
