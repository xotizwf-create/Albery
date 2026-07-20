"""Ворота перед деплоем: без прохождения — на прод не выкатываем.

Зачем: прод Albery — живая система, в которой работают люди. Каждое исправление здесь
закреплено тестом, и эти тесты защищают уже построенное от поломки следующей правкой.
Проверка запускается ЛОКАЛЬНО (не на проде): на боевой машине 2 ГБ памяти, и гонять там
тесты запрещено регламентом.

Запуск:
    python scripts/predeploy_check.py            # полная проверка
    python scripts/predeploy_check.py --quick    # без обзора изменений

Выход 0 — можно деплоить. Любой другой код — деплой запрещён.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(cmd: list[str], label: str) -> tuple[bool, str]:
    print(f"\n=== {label} ===")
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-15:])
    print(tail)
    return proc.returncode == 0, tail


def changed_files() -> list[str]:
    proc = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO,
                          capture_output=True, text=True)
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=REPO,
                            capture_output=True, text=True)
    names = set((proc.stdout or "").split()) | set((staged.stdout or "").split())
    # Удалённые файлы тоже попадают в diff — компилировать их нечем.
    return sorted(n for n in names if n.endswith(".py") and (REPO / n).is_file())


def main() -> int:
    quick = "--quick" in sys.argv[1:]
    failures: list[str] = []

    # 1. Синтаксис изменённых файлов — дешёвая проверка до всего остального.
    changed = changed_files()
    if changed:
        ok, _ = run([PY, "-m", "py_compile", *changed], f"компиляция изменённых файлов ({len(changed)})")
        if not ok:
            failures.append("изменённые файлы не компилируются")
    elif not quick:
        print("\n=== изменённых .py файлов нет (деплой уже закоммичен) ===")

    # 2. Весь тестовый пакет. Именно он охраняет уже работающее поведение.
    ok, tail = run([PY, "-m", "pytest", "-q"], "полный тестовый пакет")
    if not ok:
        failures.append("тесты не прошли")

    # 3. Приложение должно импортироваться в том же порядке, что и на проде
    #    (app первым — иначе циклический импорт zoom/app даёт ложную ошибку).
    ok, _ = run([PY, "-c", "import app, b24bot, zoom; from mcp import context_server as cs;"
                           " print('импорт ок, инструментов:', len(cs.TOOLS))"],
                "импорт как на проде")
    if not ok:
        failures.append("приложение не импортируется")

    print("\n" + "=" * 60)
    if failures:
        print("ДЕПЛОЙ ЗАПРЕЩЁН:")
        for f in failures:
            print("  -", f)
        print("Почини причину и запусти проверку заново.")
        return 1
    print("ВСЁ ЧИСТО — можно деплоить.")
    print("После деплоя обязательно прогони проверку на проде по реальным данным.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
