"""Блокировка входа после серии неверных паролей.

Правило владельца (30.07.2026): пять неверных паролей с одного адреса — и вход с этого
адреса закрыт на час. Ответ на попытку в блокировке — HTTP 429 с заголовком
``Retry-After``, чтобы браузер и человек одинаково понимали, сколько ждать.

Счётчик намеренно переживает перезапуск сервиса: он лежит в файле вне рабочего дерева
и читается при старте. Счётчик только в памяти означал бы, что обычный деплой снимает
блокировку с того, кто подбирает пароль, — а деплои у нас частые.

Отметки времени — стенные (``time.time()``), а не монотонные: монотонные часы
обнуляются вместе с процессом, и после перезапуска блокировка «уезжала» бы в будущее.

Хранилище отказоустойчивое: если файл недоступен на чтение или запись, блокировка
продолжает работать в памяти этого процесса, а вход не ломается.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Больше этого числа адресов в памяти не держим: иначе распределённый перебор
# раздувает словарь без ограничений.
MAX_TRACKED_KEYS = 10_000


class LoginLockout:
    """Счётчик неудачных входов для одной формы пароля.

    ``name`` разделяет области: у админ-панели и у рабочего окна операторов свои
    независимые блокировки и свои переменные окружения.
    """

    def __init__(
        self,
        name: str,
        *,
        attempts_env: str,
        window_env: str,
        default_attempts: int = 5,
        default_window_seconds: int = 3600,
        state_path: str | None = None,
    ) -> None:
        self.name = name
        self._attempts_env = attempts_env
        self._window_env = window_env
        self._default_attempts = default_attempts
        self._default_window = default_window_seconds
        self._explicit_state_path = state_path
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._loaded = False

    # ------------------------------------------------------------------ настройки
    def settings(self) -> tuple[int, int]:
        """(окно в секундах, число попыток до блокировки) с защитой от мусора в .env."""
        try:
            window = int(os.getenv(self._window_env, "") or self._default_window)
        except ValueError:
            window = self._default_window
        try:
            attempts = int(os.getenv(self._attempts_env, "") or self._default_attempts)
        except ValueError:
            attempts = self._default_attempts
        # Границы: окно от минуты до суток, попытки от 2 до 100. Кривое значение в .env
        # не должно ни открыть вход настежь, ни запереть владельца навсегда.
        return max(60, min(window, 86_400)), max(2, min(attempts, 100))

    def state_path(self) -> Path:
        configured = self._explicit_state_path or os.getenv("AUTH_LOCKOUT_STATE_DIR", "").strip()
        base = Path(configured) if configured else Path(tempfile.gettempdir())
        return base / f"albery_login_lockout_{self.name}.json"

    # ------------------------------------------------------------------ хранилище
    def _load_locked(self) -> None:
        """Поднять счётчик с диска. Вызывается под ``self._lock``."""
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, values in raw.items():
            if not isinstance(values, list):
                continue
            stamps = [float(item) for item in values if isinstance(item, (int, float))]
            if stamps:
                self._attempts[str(key)] = stamps

    def _save_locked(self) -> None:
        """Сбросить счётчик на диск атомарно. Вызывается под ``self._lock``."""
        path = self.state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".lockout-")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(self._attempts, stream)
                os.replace(tmp_name, path)
            except BaseException:
                # Временный файл не должен пережить неудачную запись.
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            os.chmod(path, 0o600)
        except OSError:
            # Блокировка остаётся в памяти процесса — вход в панель важнее вечности счётчика.
            logger.warning("не удалось сохранить счётчик попыток входа (%s)", self.name, exc_info=True)

    def _prune_locked(self, now: float, window: int) -> None:
        for key in list(self._attempts):
            fresh = [stamp for stamp in self._attempts[key] if now - stamp < window]
            if fresh:
                self._attempts[key] = fresh
            else:
                self._attempts.pop(key, None)
        if len(self._attempts) > MAX_TRACKED_KEYS:
            # Оставляем самые свежие: именно они означают идущий подбор.
            ordered = sorted(self._attempts.items(), key=lambda item: max(item[1]), reverse=True)
            self._attempts = dict(ordered[:MAX_TRACKED_KEYS])

    # ------------------------------------------------------------------ публичное API
    def check(self, key: str) -> tuple[bool, int]:
        """(заблокирован ли адрес, сколько секунд ждать).

        Попытка в блокировке НЕ продлевает её: иначе тот, кто долбится в дверь,
        держал бы себя заблокированным вечно, а честный человек с того же адреса
        (офисный NAT) не смог бы дождаться конца часа.
        """
        window, maximum = self.settings()
        now = time.time()
        with self._lock:
            self._load_locked()
            self._prune_locked(now, window)
            recent = self._attempts.get(key, [])
            if len(recent) < maximum:
                return False, 0
            retry_after = max(1, int(window - (now - min(recent))))
            return True, retry_after

    def record_failure(self, key: str) -> None:
        window, _maximum = self.settings()
        now = time.time()
        with self._lock:
            self._load_locked()
            self._prune_locked(now, window)
            self._attempts.setdefault(key, []).append(now)
            self._save_locked()

    def clear(self, key: str) -> None:
        """Успешный вход снимает счётчик с адреса."""
        with self._lock:
            self._load_locked()
            if self._attempts.pop(key, None) is not None:
                self._save_locked()

    def reset_all(self) -> None:
        """Полный сброс — для тестов и для ручного снятия блокировки."""
        with self._lock:
            self._attempts.clear()
            self._loaded = True
            self._save_locked()


# Блокировка входа в админ-панель Albery.
admin_lockout = LoginLockout(
    "admin",
    attempts_env="AUTH_RATE_LIMIT_ATTEMPTS",
    window_env="AUTH_RATE_LIMIT_WINDOW_SECONDS",
    default_attempts=5,
    default_window_seconds=3600,
)
