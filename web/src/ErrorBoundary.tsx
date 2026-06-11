import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Top-level error boundary. Catches render-phase throws anywhere in the
 * wrapped subtree, logs them, and shows a calm branded fallback instead of
 * a white screen. Use getDerivedStateFromError (synchronous, no side-effects)
 * to flip the gate, and componentDidCatch to log the full error + component
 * stack so it shows up in the browser console and any wired logger.
 */
export default class ErrorBoundary extends React.Component<Props, State> {
  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error("[ErrorBoundary] Uncaught render error:", error);
    console.error("[ErrorBoundary] Component stack:", info.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  override render(): React.ReactNode {
    if (!this.state?.hasError) {
      return this.props.children;
    }

    const wrapStyle: React.CSSProperties = {
      display: "grid",
      placeItems: "center",
      minHeight: "100vh",
      background: "var(--bg, #f9f8f7)",
      fontFamily: "system-ui, sans-serif",
      padding: "24px",
    };

    const cardStyle: React.CSSProperties = {
      background: "var(--surface, #ffffff)",
      border: "1px solid var(--line, #e8e4df)",
      borderRadius: "14px",
      boxShadow: "0 4px 12px oklch(0.4 0.02 60 / 0.07), 0 2px 4px oklch(0.4 0.02 60 / 0.05)",
      padding: "40px 36px",
      maxWidth: "420px",
      width: "100%",
      textAlign: "center",
    };

    const headingStyle: React.CSSProperties = {
      fontSize: "18px",
      fontWeight: 700,
      color: "var(--ink, #2a2622)",
      margin: "0 0 10px",
    };

    const bodyStyle: React.CSSProperties = {
      fontSize: "14px",
      color: "var(--ink-3, #8a8278)",
      lineHeight: 1.6,
      margin: "0 0 24px",
    };

    const buttonStyle: React.CSSProperties = {
      display: "inline-block",
      padding: "9px 20px",
      borderRadius: "10px",
      border: "none",
      background: "var(--ink, #2a2622)",
      color: "#fff",
      fontSize: "13.5px",
      fontWeight: 650,
      cursor: "pointer",
      lineHeight: 1.4,
    };

    return (
      <div style={wrapStyle} role="alert" aria-live="assertive">
        <div style={cardStyle}>
          <h1 style={headingStyle}>Something went wrong</h1>
          <p style={bodyStyle}>
            An unexpected error occurred. Your data is safe — reload to try again.
          </p>
          <button
            type="button"
            style={buttonStyle}
            onClick={this.handleReload}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = "0.85";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = "1";
            }}
          >
            Reload
          </button>
        </div>
      </div>
    );
  }

  // Default state so the guard in render() is always well-typed.
  override state: State = { hasError: false, error: null };
}
