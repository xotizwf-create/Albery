from pathlib import Path


FRONTEND = (
    Path(__file__).resolve().parents[2]
    / "Интерфейс"
    / "src"
    / "funnel-workspace"
    / "FunnelWorkspace.tsx"
)
FRONTEND_ROOT = FRONTEND.parents[2]
INDEX_HTML = FRONTEND_ROOT / "index.html"
MAIN_TSX = FRONTEND_ROOT / "src" / "main.tsx"
ROOT_ERROR_BOUNDARY = FRONTEND_ROOT / "src" / "RootErrorBoundary.tsx"


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


def test_russian_spa_disables_browser_translation_that_mutates_react_dom():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert '<html lang="ru" translate="no" class="notranslate">' in html
    assert '<meta name="google" content="notranslate" />' in html
    assert '<body translate="no" class="notranslate">' in html
    assert '<div id="root" translate="no" class="notranslate"></div>' in html


def test_root_render_and_bootstrap_failures_never_leave_a_blank_page():
    main = MAIN_TSX.read_text(encoding="utf-8")
    boundary = ROOT_ERROR_BOUNDARY.read_text(encoding="utf-8")

    assert "<RootErrorBoundary>" in main
    assert "</RootErrorBoundary>" in main
    assert "bootstrap().catch(showBootstrapFailure)" in main
    assert 'data-ui-recovery="render"' in boundary
    assert 'page.dataset.uiRecovery = "bootstrap"' in boundary
    assert "Обновить страницу" in boundary
    assert "root.replaceChildren(page)" in boundary
