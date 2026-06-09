// The ONE reviewed place that renders HTML strings. All feed/activity HTML (e.g. `f.html` from the
// in-app data) flows through here, sanitized with DOMPurify, so a malicious string can never inject
// script or event handlers. This replaces the raw dangerouslySetInnerHTML usages in the prototype
// screens (app/dashboard/security). If you need to render an HTML string anywhere, use this — never
// dangerouslySetInnerHTML directly.
import DOMPurify from "dompurify";
import React from "react";

type SafeHtmlProps = {
  html: string | null | undefined;
  as?: keyof JSX.IntrinsicElements;
} & React.HTMLAttributes<HTMLElement>;

export function SafeHtml({ html, as = "span", ...rest }: SafeHtmlProps) {
  // Strip scripts, event handlers (onerror, onclick, ...), and javascript: URLs. Allow basic markup.
  const clean = DOMPurify.sanitize(html ?? "", {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "script", "iframe", "object", "embed", "form"],
    FORBID_ATTR: ["style"],
  });
  const Tag = as as any;
  return <Tag {...rest} dangerouslySetInnerHTML={{ __html: clean }} />;
}
