from pathlib import Path


FRONTEND = (
    Path(__file__).resolve().parents[2]
    / "Интерфейс"
    / "src"
    / "funnel-workspace"
    / "FunnelWorkspace.tsx"
)


def test_login_uses_the_server_bound_operator_name_for_button_and_submit():
    source = FRONTEND.read_text(encoding="utf-8")
    login = source.split(
        "function WorkspaceLogin(",
        maxsplit=1,
    )[1].split(
        "function WorkspacePasswordSetup(",
        maxsplit=1,
    )[0]

    assert "configuredOperatorName?.trim() || operatorName.trim()" in login
    assert "!resolvedOperatorName" in login
    assert "funnelWorkspaceApi.login(" in login
    assert "resolvedOperatorName," in login
    assert "!operatorName.trim()" not in login
    assert 'name="password"' in login
    assert "onInput={(event) => setPassword(event.currentTarget.value)}" in login


def test_close_question_returns_the_active_dialog_to_ai_instead_of_closing_it():
    source = FRONTEND.read_text(encoding="utf-8")
    handler = source.split(
        "const closeConversationQuestion = async",
        maxsplit=1,
    )[1].split(
        "const deleteConversation = async",
        maxsplit=1,
    )[0]

    assert "setConversationControl(" in handler
    assert '"ai"' in handler
    assert "restoreMainMenu: true" in handler
    assert "loadConversations(true, true)" in handler
    assert "funnelWorkspaceApi.setStatus" not in handler
    assert 'status: "closed"' not in handler
