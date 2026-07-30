from pathlib import Path


FRONTEND = (
    Path(__file__).resolve().parents[2]
    / "Интерфейс"
    / "src"
    / "funnel-workspace"
    / "FunnelWorkspace.tsx"
)


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
