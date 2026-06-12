import React from "react";

interface Props {
  children: React.ReactNode;
  /**
   * Route-scoped (in-shell) mode. When true the fallback renders at ~60vh
   * INSIDE the viewport (no full-page background), so a single broken surface
   * keeps the sidebar/topbar/chat alive while the rest of the app navigates.
   * When false/undefined it renders the full-page (100vh) catch-all fallback.
   */
  compact?: boolean;
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

  // Compact (route-scoped) retry: clear the error so the wrapped surface
  // re-renders in place, without a full page reload that would lose shell state.
  private handleRetry = (): void => {
    this.setState({ hasError: false, error: null });
  };

  override render(): React.ReactNode {
    if (!this.state?.hasError) {
      return this.props.children;
    }

    const compact = this.props.compact === true;

    const wrapStyle: React.CSSProperties = {
      display: "grid",
      placeItems: "center",
      minHeight: compact ? "60vh" : "100vh",
      // Route-scoped fallback sits inside the live viewport, so it must not paint
      // a full-page background over the surrounding shell chrome.
      ...(compact ? {} : { background: "var(--bg, #f9f8f7)" }),
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

    const heading = compact ? "Something went wrong on this screen" : "Something went wrong";
    const body = compact
      ? "This screen hit an unexpected error. The rest of the app is still working, so you can switch to another area, or try this screen again."
      : "An unexpected error occurred. Your data is safe — reload to try again.";
    const buttonLabel = compact ? "Try again" : "Reload";
    const onButton = compact ? this.handleRetry : this.handleReload;

    return (
      <div
        style={wrapStyle}
        role="alert"
        aria-live="assertive"
        data-testid={compact ? "route-error-boundary" : "error-boundary"}
      >
        <div style={cardStyle}>
          <h1 style={headingStyle}>{heading}</h1>
          <p style={bodyStyle}>{body}</p>
          <button
            type="button"
            style={buttonStyle}
            onClick={onButton}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = "0.85";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.opacity = "1";
            }}
          >
            {buttonLabel}
          </button>
        </div>
      </div>
    );
  }

  // Default state so the guard in render() is always well-typed.
  override state: State = { hasError: false, error: null };
}
