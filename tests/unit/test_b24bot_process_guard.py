import signal


def test_hermes_run_starts_posix_process_group(monkeypatch):
    import b24bot

    popen_kwargs = {}

    class FakeChild:
        returncode = 0

        def communicate(self, timeout=None):
            return "ok", ""

    def fake_popen(**kwargs):
        popen_kwargs.update(kwargs)
        return FakeChild()

    monkeypatch.setattr(b24bot.os, "name", "posix")
    monkeypatch.setattr(b24bot.subprocess, "Popen", fake_popen)

    proc, error = b24bot._hermes_run_guarded(
        ["hermes", "-z", "ok"],
        1,
        "dialog",
        "faq",
        "user",
        2,
        scope="test-process-group",
    )

    assert error is None
    assert proc.returncode == 0
    assert proc.stdout == "ok"
    assert popen_kwargs["start_new_session"] is True
    assert popen_kwargs["args"] == ["hermes", "-z", "ok"]


def test_cancel_live_turn_kills_posix_process_group(monkeypatch):
    import b24bot

    calls = []

    class FakeProc:
        pid = 1234

        def poll(self):
            return None

        def kill(self):
            calls.append(("kill",))

    monkeypatch.setattr(b24bot.os, "name", "posix")
    monkeypatch.setattr(b24bot.os, "getpgid", lambda pid: 5678, raising=False)
    monkeypatch.setattr(
        b24bot.os,
        "killpg",
        lambda pgid, sig: calls.append(("killpg", pgid, sig)),
        raising=False,
    )

    scope = "test-cancel-process-group"
    with b24bot._LIVE_TURNS_LOCK:
        b24bot._LIVE_TURNS[scope] = [{"proc": FakeProc(), "cancelled": False}]

    try:
        assert b24bot._b24_cancel_live_turns(scope) == 1
    finally:
        with b24bot._LIVE_TURNS_LOCK:
            b24bot._LIVE_TURNS.pop(scope, None)

    assert calls == [("killpg", 5678, getattr(signal, "SIGKILL", signal.SIGTERM))]
