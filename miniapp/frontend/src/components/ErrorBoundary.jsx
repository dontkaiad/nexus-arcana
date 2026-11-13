import React from "react";

// Ловит ошибки РЕНДЕРА (ReferenceError и т.п.) в поддереве. Без границы React 18
// размонтирует всё приложение и пользователь видит пустой экран.
//  - inline: компактный блок на месте упавшей формы/шита (кнопка «Назад» → onBack)
//  - иначе: полноэкранный fallback (кнопка «Назад» → onBack или перезагрузка)
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error(`ErrorBoundary[${this.props.label || "app"}]:`, error, info && info.componentStack);
  }

  back = () => {
    const { onBack } = this.props;
    this.setState({ error: null });
    if (onBack) onBack();
    else if (typeof window !== "undefined") window.location.reload();
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const detail = String((error && error.message) || error).slice(0, 160);
    const box = this.props.inline
      ? { padding: "16px 4px", textAlign: "center", color: "inherit" }
      : {
          position: "fixed", inset: 0, zIndex: 1000, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 12, padding: 24, textAlign: "center",
          background: "#f0e7d8", color: "#3a2f24",
        };
    return (
      <div role="alert" style={box}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Что-то сломалось</div>
        <div style={{ fontSize: 12, opacity: 0.65, maxWidth: 320, wordBreak: "break-word" }}>{detail}</div>
        <button
          type="button"
          onClick={this.back}
          style={{
            padding: "10px 22px", borderRadius: 12, border: "1px solid rgba(0,0,0,0.15)",
            background: "#b8822a", color: "#fff", fontSize: 14, cursor: "pointer",
          }}
        >
          ← Назад
        </button>
      </div>
    );
  }
}
