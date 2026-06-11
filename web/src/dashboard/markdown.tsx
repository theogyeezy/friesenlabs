// Safe markdown SUBSET renderer for the markdown-note catalog component.
//
// SPEC, NOT CODE: the note body is untrusted data from a view-spec. This module
// parses a deliberately tiny markdown subset and emits REACT NODES ONLY — there
// is no HTML string, no dangerouslySetInnerHTML, no link/image/embed support
// (links can smuggle javascript: URLs; images can beacon). Anything the parser
// does not recognize renders as literal escaped text, which React guarantees.
//
// Supported, and nothing else:
//   #, ##, ###  headings
//   - item      bullet lists
//   1. item     ordered lists
//   **bold**  *italic*  `code`   inline spans
//   blank-line-separated paragraphs

import React from "react";

// ---------------------------------------------------------------------------
// Inline spans: **bold**, *italic*, `code`. Single pass, no nesting, no HTML.
// ---------------------------------------------------------------------------

const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;

export function renderInline(text: string): React.ReactNode[] {
  const parts = text.split(INLINE_RE);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={i}
          style={{
            fontSize: "0.92em",
            background: "var(--line-2, #efe9df)",
            borderRadius: 4,
            padding: "1px 5px",
          }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    // Plain text (React escapes it).
    return <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

// ---------------------------------------------------------------------------
// Block structure
// ---------------------------------------------------------------------------

type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] }
  | { kind: "p"; text: string };

function parseBlocks(body: string): Block[] {
  const blocks: Block[] = [];
  const lines = body.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    if (trimmed === "") {
      i += 1;
      continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed);
    if (heading) {
      blocks.push({
        kind: "heading",
        level: heading[1].length as 1 | 2 | 3,
        text: heading[2],
      });
      i += 1;
      continue;
    }
    if (/^-\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^-\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^-\s+/, ""));
        i += 1;
      }
      blocks.push({ kind: "ul", items });
      continue;
    }
    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push({ kind: "ol", items });
      continue;
    }
    // Paragraph: greedily absorb until a blank line or a structural line.
    const para: string[] = [];
    while (i < lines.length) {
      const t = lines[i].trim();
      if (t === "" || /^(#{1,3})\s+/.test(t) || /^-\s+/.test(t) || /^\d+\.\s+/.test(t)) break;
      para.push(t);
      i += 1;
    }
    blocks.push({ kind: "p", text: para.join(" ") });
  }
  return blocks;
}

const headingStyle: Record<number, React.CSSProperties> = {
  1: { fontSize: 16, fontWeight: 760, margin: "10px 0 4px", color: "var(--ink, #2a2622)" },
  2: { fontSize: 14.5, fontWeight: 720, margin: "8px 0 4px", color: "var(--ink, #2a2622)" },
  3: { fontSize: 13.5, fontWeight: 700, margin: "8px 0 2px", color: "var(--ink, #2a2622)" },
};

/** Render the markdown subset to React nodes. Pure data-to-nodes, no HTML sink. */
export function SafeMarkdown({ body }: { body: string }) {
  const blocks = parseBlocks(body);
  const text: React.CSSProperties = {
    fontSize: 13,
    lineHeight: 1.55,
    color: "var(--ink-2, #55504a)",
    margin: "4px 0",
  };
  return (
    <div data-testid="safe-markdown">
      {blocks.map((b, i) => {
        if (b.kind === "heading") {
          // Note headings are visual only (not document outline): keep them
          // out of the page heading order with role="presentation" divs.
          return (
            <div key={i} role="presentation" style={headingStyle[b.level]}>
              {renderInline(b.text)}
            </div>
          );
        }
        if (b.kind === "ul" || b.kind === "ol") {
          const items = b.items.map((item, j) => (
            <li key={j} style={{ margin: "2px 0" }}>
              {renderInline(item)}
            </li>
          ));
          return b.kind === "ul" ? (
            <ul key={i} style={{ ...text, paddingLeft: 20 }}>
              {items}
            </ul>
          ) : (
            <ol key={i} style={{ ...text, paddingLeft: 20 }}>
              {items}
            </ol>
          );
        }
        return (
          <p key={i} style={text}>
            {renderInline(b.text)}
          </p>
        );
      })}
    </div>
  );
}

export default SafeMarkdown;
