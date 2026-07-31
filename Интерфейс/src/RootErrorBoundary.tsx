import {
  Component,
  type CSSProperties,
  type ErrorInfo,
  type ReactNode,
} from "react";

type RootErrorBoundaryProps = {
  children: ReactNode;
};

type RootErrorBoundaryState = {
  failed: boolean;
};

const pageStyle: CSSProperties = {
  alignItems: "center",
  background: "#f8fafc",
  color: "#0f172a",
  display: "flex",
  fontFamily:
    'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  justifyContent: "center",
  minHeight: "100vh",
  padding: "24px",
};

const cardStyle: CSSProperties = {
  background: "#ffffff",
  border: "1px solid #e2e8f0",
  borderRadius: "18px",
  boxShadow: "0 20px 45px rgba(15, 23, 42, 0.08)",
  boxSizing: "border-box",
  maxWidth: "520px",
  padding: "32px",
  textAlign: "center",
  width: "100%",
};

const buttonStyle: CSSProperties = {
  background: "#7c3aed",
  border: 0,
  borderRadius: "10px",
  color: "#ffffff",
  cursor: "pointer",
  font: "inherit",
  fontWeight: 700,
  marginTop: "22px",
  minHeight: "44px",
  padding: "11px 20px",
};

const titleStyle: CSSProperties = {
  fontSize: "22px",
  lineHeight: 1.25,
  margin: 0,
};

const textStyle: CSSProperties = {
  color: "#475569",
  fontSize: "15px",
  lineHeight: 1.55,
  margin: "12px 0 0",
};

function RecoveryScreen() {
  return (
    <main
      data-ui-recovery="render"
      role="alert"
      aria-live="assertive"
      style={pageStyle}
      translate="no"
      className="notranslate"
    >
      <section style={cardStyle}>
        <h1 style={titleStyle}>Не удалось отобразить страницу</h1>
        <p style={textStyle}>
          Обновите её — ваши данные и переписка сохранятся.
        </p>
        <button type="button" style={buttonStyle} onClick={() => window.location.reload()}>
          Обновить страницу
        </button>
      </section>
    </main>
  );
}

/**
 * Последний предохранитель React-дерева. Ошибка отдельного экрана больше не
 * превращается в безымянную белую страницу: человек получает безопасный путь
 * восстановления, а исходная ошибка остаётся в консоли для диагностики.
 */
export class RootErrorBoundary extends Component<
  RootErrorBoundaryProps,
  RootErrorBoundaryState
> {
  declare readonly props: RootErrorBoundaryProps;
  state: RootErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): RootErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("[Albery UI] Ошибка рендера", error, info);
  }

  render() {
    return this.state.failed ? <RecoveryScreen /> : this.props.children;
  }
}

const applyStyle = (element: HTMLElement, style: CSSProperties) => {
  Object.assign(element.style, style);
};

/**
 * Динамический импорт выполняется до первого React render, поэтому обычная
 * ErrorBoundary его не поймает. Показываем тот же понятный fallback напрямую.
 */
export function showBootstrapFailure(error: unknown) {
  console.error("[Albery UI] Ошибка загрузки", error);
  const root = document.getElementById("root");
  if (!root) return;

  const page = document.createElement("main");
  page.dataset.uiRecovery = "bootstrap";
  page.setAttribute("role", "alert");
  page.setAttribute("aria-live", "assertive");
  page.setAttribute("translate", "no");
  page.className = "notranslate";
  applyStyle(page, pageStyle);

  const card = document.createElement("section");
  applyStyle(card, cardStyle);

  const title = document.createElement("h1");
  title.textContent = "Не удалось загрузить страницу";
  applyStyle(title, titleStyle);

  const text = document.createElement("p");
  text.textContent = "Проверьте соединение и обновите страницу. Ваши данные сохранятся.";
  applyStyle(text, textStyle);

  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Обновить страницу";
  applyStyle(button, buttonStyle);
  button.addEventListener("click", () => window.location.reload());

  card.append(title, text, button);
  page.append(card);
  root.replaceChildren(page);
}
