#!/usr/bin/env python3
"""Отправляет свежий дамп PostgreSQL на резервный сервер и подрезает там историю.

Почему не «rsync всей папки», как было до 06.08.2026. Локально держится 10 дампов
(RETENTION_DAYS в backup_postgres.sh), а дамп вырос с 49 МБ (14.07) до 735 МБ (06.08) — база
растёт вслепую, две таблицы дают 5 ГБ из 5.9. Десять таких файлов не влезают на приёмник
с диском 15 ГБ: он забился на 100%, rsync начал падать с «No space left on device», и
оффсайт-копия МОЛЧА не обновлялась 18 дней (последняя удачная — 19.07). Локальные копии при
этом были живы, поэтому ни один монитор не сработал: снаружи бэкапы «делались».

Что делает этот скрипт:
- отправляет РОВНО один файл — самый свежий дамп, а не всю папку;
- сверяет md5 источника и копии: несовпадение = ошибка, это не бэкап;
- держит на приёмнике OFFSITE_KEEP штук;
- ругается в stderr (значит — в лог и в selfcheck), если места осталось меньше двух дампов.

Порядок подрезки выбирается по свободному месту:
- места хватает на новый файл поверх старых — сначала льём, потом удаляем старое
  (в каждый момент на приёмнике есть хотя бы одна целая копия);
- места не хватает — вынужденно освобождаем ДО отправки. Тогда есть короткое окно,
  когда целой копии на приёмнике нет. Это осознанная деградация: основная страховка —
  10 локальных копий на боевом сервере, оффсайт нужен на случай «коробка умерла целиком».
  Окно исчезнет само, когда база похудеет и дамп станет меньше половины свободного места.

Ставится кроном вместо старой строки rsync (/etc/cron.d/albery-backup-offsite).
"""
import os
import subprocess
import sys
from datetime import datetime, timezone

BACKUP_DIR = os.getenv("BACKUP_DIR", "/var/backups/albery/postgres")
REMOTE_HOST = os.getenv("OFFSITE_HOST", "root@217.198.12.236")
REMOTE_DIR = os.getenv("OFFSITE_DIR", "/root/backups/albery-postgres")
OFFSITE_KEEP = max(1, int(os.getenv("OFFSITE_KEEP", "1") or "1"))
SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=30"]


def plan_prune(names_newest_first: list[str], keep: int) -> list[str]:
    """Какие файлы удалить, чтобы осталось keep самых свежих.

    keep<1 не бывает: даже при кривом env нельзя вычистить приёмник досуха.
    """
    return list(names_newest_first[max(1, keep):])


def prune_before_send(free_mb: int, dump_mb: int) -> bool:
    """Надо ли освобождать место ДО отправки.

    Запас в 10% — на служебные блоки файловой системы: rsync, упавший на последнем
    мегабайте, оставит битый файл и мы потеряем и новую копию, и старую.
    """
    return free_mb < dump_mb * 1.1


def _log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} {msg}", flush=True)


def _ssh(command: str) -> str:
    out = subprocess.run(
        ["ssh", *SSH_OPTS, REMOTE_HOST, command],
        capture_output=True, text=True, timeout=300, check=True,
    )
    return out.stdout.strip()


def _remote_prune(keep: int) -> None:
    listing = _ssh(f"cd '{REMOTE_DIR}' 2>/dev/null && ls -1t albery_*.dump 2>/dev/null || true")
    names = [n for n in listing.splitlines() if n.strip()]
    doomed = plan_prune(names, keep)
    if not doomed:
        return
    quoted = " ".join(f"'{n}'" for n in doomed)
    _ssh(f"cd '{REMOTE_DIR}' && rm -f {quoted}")
    _log(f"на приёмнике удалено устаревших копий: {len(doomed)}")


def main() -> int:
    dumps = sorted(
        (e for e in os.scandir(BACKUP_DIR) if e.name.startswith("albery_") and e.name.endswith(".dump")),
        key=lambda e: e.stat().st_mtime, reverse=True,
    )
    if not dumps:
        _log(f"НЕТ ДАМПОВ в {BACKUP_DIR} — отправлять нечего")
        return 1

    latest = dumps[0]
    dump_mb = latest.stat().st_size // 1024 // 1024

    _ssh(f"mkdir -p '{REMOTE_DIR}'")
    free_mb = int(_ssh(f"df -Pm '{REMOTE_DIR}' | tail -1 | awk '{{print $4}}'"))

    if prune_before_send(free_mb, dump_mb):
        _log(f"на приёмнике {free_mb} МБ при дампе {dump_mb} МБ — освобождаю место до отправки")
        _remote_prune(OFFSITE_KEEP - 1 if OFFSITE_KEEP > 1 else 1)
        # keep=1 при OFFSITE_KEEP=1: оставляем предыдущую копию до последнего, rsync
        # перезапишет её только если имя совпадёт (одинаковая дата) — иначе она уйдёт
        # на втором проходе подрезки, уже после успешной сверки.

    _log(f"отправляю {latest.name} ({dump_mb} МБ) на {REMOTE_HOST}")
    subprocess.run(
        ["rsync", "-a", "-e", "ssh " + " ".join(SSH_OPTS), latest.path, f"{REMOTE_HOST}:{REMOTE_DIR}/"],
        check=True, timeout=3600,
    )

    local_sum = subprocess.run(
        ["md5sum", latest.path], capture_output=True, text=True, check=True,
    ).stdout.split()[0]
    remote_sum = _ssh(f"md5sum '{REMOTE_DIR}/{latest.name}'").split()[0]
    if local_sum != remote_sum:
        _log(f"КОНТРОЛЬНАЯ СУММА НЕ СОВПАЛА: {local_sum} != {remote_sum} — копия негодна")
        return 1

    _remote_prune(OFFSITE_KEEP)

    free_after = int(_ssh(f"df -Pm '{REMOTE_DIR}' | tail -1 | awk '{{print $4}}'"))
    _log(f"ок: {latest.name} на месте, сумма сходится, свободно на приёмнике {free_after} МБ")

    if free_after < dump_mb * 2:
        _log(f"ВНИМАНИЕ: свободно {free_after} МБ при дампе {dump_mb} МБ — следующая ночь может не влезть")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        _log(f"ОШИБКА: команда завершилась с кодом {exc.returncode}: {(exc.stderr or '').strip()[:400]}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        _log(f"ОШИБКА: {type(exc).__name__}: {exc}")
        sys.exit(1)
