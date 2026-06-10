// Demo proving feed HTML is sanitized: a malicious payload renders inert while
// safe markup survives (e2e/safehtml.spec.ts asserts window.__pwned stays
// unset). MOCK BUILDS ONLY: this module is reached exclusively through the
// build-time-gated lazy import in main.tsx, so the __pwned probe strings and
// the deliberately malicious payload below never ship in a real-mode
// production bundle.

import React from "react";
import { SafeHtml } from "../lib/SafeHtml";

export default function SafeHtmlDemo() {
  const payload =
    '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1<\/script><b>safe bold</b>';
  return (
    <div style={{ padding: 24 }}>
      <h1>SafeHtml</h1>
      <div data-testid="feed">
        <SafeHtml as="p" html={payload} />
      </div>
    </div>
  );
}
