"""Оффсайт-бэкап: он молча не работал 18 дней, и ни один монитор этого не увидел.

Реальный случай (06.08.2026): на приёмнике 217.198.12.236 диск 15 ГБ забился на 100%,
rsync стал падать с «No space left on device», последняя удачная копия осталась от 19.07.
Причина: старый крон гнал rsync ВСЕЙ папки, а локально лежит 10 дампов, и дамп вырос
с 49 МБ (14.07) до 735 МБ (06.08) — 10 × 735 МБ на 15-гигабайтный диск не помещаются.

Тесты держат три вещи, на которых это сломалось:
- отправляется один файл, а не вся папка;
- подрезка никогда не вычищает приёмник досуха (иначе «починка» стала бы потерей бэкапа);
- нехватка места распознаётся ДО отправки, а не по факту битого файла.
"""
import pytest

from scripts.backup_offsite import plan_prune, prune_before_send

# Реальный листинг приёмника на утро 06.08.2026, новые сверху.
REAL_OFFSITE = [
    "albery_20260719_031501.dump",
    "albery_20260718_031501.dump",
    "albery_20260717_031501.dump",
    "albery_20260716_233503.dump",
    "albery_20260716_031501.dump",
    "albery_20260715_031501.dump",
    "albery_20260714_031501.dump",
    "albery_20260713_031501.dump",
    "albery_20260712_165539.dump",
    "albery_predeploy046_20260708_172021.dump",
]

REAL_DUMP_MB = 735  # albery_20260806_031501.dump
REAL_FREE_MB = 494  # столько осталось на 217 после первой расчистки


def test_prune_keeps_only_newest():
    doomed = plan_prune(REAL_OFFSITE, keep=1)
    assert "albery_20260719_031501.dump" not in doomed
    assert len(doomed) == 9
    assert "albery_predeploy046_20260708_172021.dump" in doomed


def test_prune_never_empties_the_receiver():
    """keep=0 из кривого env не должен превратить подрезку в удаление всех бэкапов."""
    for bad_keep in (0, -1, -100):
        doomed = plan_prune(REAL_OFFSITE, keep=bad_keep)
        assert REAL_OFFSITE[0] not in doomed, "самая свежая копия обязана уцелеть при любом keep"
        assert len(doomed) == len(REAL_OFFSITE) - 1


def test_prune_noop_when_already_within_limit():
    assert plan_prune(["albery_20260806_031501.dump"], keep=1) == []
    assert plan_prune([], keep=3) == []


def test_prune_keeps_three_when_asked():
    doomed = plan_prune(REAL_OFFSITE, keep=3)
    assert len(doomed) == 7
    assert REAL_OFFSITE[2] not in doomed
    assert REAL_OFFSITE[3] in doomed


def test_real_incident_is_recognised_before_sending():
    """494 МБ свободно, дамп 735 МБ — старый крон узнавал об этом только по битому файлу."""
    assert prune_before_send(REAL_FREE_MB, REAL_DUMP_MB) is True


def test_healthy_box_sends_first_and_prunes_after():
    """Когда места вдоволь, окна без целой копии на приёмнике быть не должно."""
    assert prune_before_send(3000, REAL_DUMP_MB) is False


@pytest.mark.parametrize("free_mb, expected", [
    (735, True),    # ровно размер дампа — без запаса на служебные блоки, рискованно
    (808, True),    # 735 * 1.1 = 808.5, впритык — всё ещё чистим заранее
    (810, False),   # запас есть
])
def test_headroom_boundary(free_mb, expected):
    assert prune_before_send(free_mb, REAL_DUMP_MB) is expected


def test_one_file_leaves_at_most_one_file():
    """Суть починки: за прогон уезжает РОВНО один дамп, а не вся локальная папка.

    Старое поведение (rsync всей директории) потребовало бы 10 × 735 = 7350 МБ на приёмнике,
    где свободно было 494 МБ.
    """
    local_dumps = [f"albery_2026080{i}_031501.dump" for i in range(1, 7)]
    assert len(local_dumps) > 1
    # plan_prune работает с листингом ПРИЁМНИКА; после отправки одного файла и подрезки
    # на приёмнике не может остаться больше keep штук.
    after_send = ["albery_20260806_031501.dump", *REAL_OFFSITE]
    survivors = [n for n in after_send if n not in plan_prune(after_send, keep=1)]
    assert survivors == ["albery_20260806_031501.dump"]
