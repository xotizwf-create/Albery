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
