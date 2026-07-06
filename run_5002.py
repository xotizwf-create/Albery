import os
import signal
import threading
import time

from app import app

_draining = threading.Event()


def _inflight_count() -> int:
    """How many brain turns are running right now (durable registry). 0 on any error so a
    broken query can never wedge shutdown."""
    try:
        from app import pg_connect
        with pg_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) AS c FROM bitrix_inflight_turns")
                return int((cur.fetchone() or {}).get("c") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _graceful_stop(_signum, _frame):
    """On SIGTERM (systemd stop/restart), give in-flight brain turns a short grace to finish
    before the process dies, so a deploy restart doesn't cut a user off mid-answer. Bounded by
    ALBERY_DRAIN_SECONDS so a long turn can't block the deploy — anything still running past the
    deadline is caught by the boot recovery net (bitrix_inflight_turns) and the user is asked to
    resend. A second SIGTERM exits immediately."""
    if _draining.is_set():
        os._exit(0)
    _draining.set()
    deadline = time.time() + int(os.getenv("ALBERY_DRAIN_SECONDS", "25"))
    while time.time() < deadline and _inflight_count() > 0:
        time.sleep(1)
    os._exit(0)


signal.signal(signal.SIGTERM, _graceful_stop)

app.run(host="127.0.0.1", port=5002, debug=False, use_reloader=False, threaded=True)
