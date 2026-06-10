// Small, self-contained loading spinner for the API-wired surfaces. The
// keyframes ride along inline so the spinner works in any mount (the full
// shell or a bare ?view= seam) without depending on styles.css being loaded.

import React from "react";

export interface SpinnerProps {
  /** Visible label next to the spinner; also the accessible name. */
  label?: string;
  size?: number;
  testid?: string;
}

export function Spinner({ label, size = 22, testid }: SpinnerProps) {
  return (
    <div
      data-testid={testid}
      role="status"
      aria-label={label ?? "Loading"}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        color: "var(--ink-3, #8a8278)",
        fontSize: 13,
        padding: "10px 0",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          border: "3px solid var(--line, #e3ddd3)",
          borderTopColor: "var(--accent, #2a2622)",
          animation: "uplift-spin .8s linear infinite",
          flexShrink: 0,
          boxSizing: "border-box",
        }}
      />
      {label && <span>{label}</span>}
      <style>{"@keyframes uplift-spin{to{transform:rotate(360deg)}}"}</style>
    </div>
  );
}

export default Spinner;
