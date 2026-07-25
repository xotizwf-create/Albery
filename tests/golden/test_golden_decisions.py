"""Золотой набор: реальные реплики клиентов → решение, которое реестр обязан принять (фаза 4).

Владелец 25.07.2026: «нужны реальные инженерные решения». Тесты правил проверяют правила по
одному; здесь проверяется, что на ЖИВОМ языке клиентов система ведёт себя так, как задумано.
Набор собран из переписки на проде за 10 дней и просмотрен вручную.

Если правка меняет любое из этих решений — тест назовёт реплику и покажет разницу. Это не запрет
на изменения: это требование делать их осознанно, видя, что именно поменяется для клиентов.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import funnel_rules as fr

GOLDEN = json.loads((pathlib.Path(__file__).with_name("decisions.json")).read_text(encoding="utf-8"))
CASES = GOLDEN["случаи"]


@pytest.mark.parametrize("case", CASES, ids=[c["текст"][:40] for c in CASES])
def test_golden_decision(case):
    facts = fr.Facts(uid=1, text=case["текст"],
                     terms_sent=case["условия_отправлены"],
                     wants_terms=case["модель_просит_условия"],
                     deal_status_unknown=case.get("CRM_статус_неизвестен", False))

    decision = fr.decide(facts)

    assert decision.rule == case["правило"], (
        f"реплика «{case['текст'][:70]}»: ожидали правило «{case['правило']}», "
        f"получили «{decision.rule}». " + (case.get("почему") or ""))
    assert decision.action == case["действие"]


def test_golden_set_covers_every_message_rule():
    """Правило без живого примера в наборе — слепое пятно: его поведение никто не проверял."""
    covered = {c["правило"] for c in CASES}
    message_rules = {r.name for r in fr.RULES if r.slot == "message"}

    assert message_rules <= covered, f"нет живых примеров для: {message_rules - covered}"


def test_golden_set_is_built_from_real_dialogs():
    """Набор обязан быть из реальной переписки, иначе он проверяет фантазии, а не жизнь."""
    assert "РЕАЛЬНЫЕ реплики" in GOLDEN["_описание"]
    assert len(CASES) >= 15, "набор слишком мал, чтобы что-то стеречь"
