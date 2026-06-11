// HelpForm — the in-app "Help / Contact support" form.
//
// Two presentations, one body:
//   - <HelpDialog onClose> : a modal (the footer/help-button entry point), with
//     dialog role + Escape-to-close + scrim-click-to-close, matching the landing
//     modals' a11y contract.
//   - <HelpPage> : a standalone page (the ?view=help seam) for a deep-linkable
//     "/help" surface.
//
// Posts to POST /public/support via submitSupport (web/src/support/api.ts). The
// confirmation is HONEST: a success message appears ONLY when the server
// accepted the request (ok === true); otherwise the user is offered an
// honest mailto: fallback so the request never silently vanishes. No fake
// "we got it" is ever shown.

import React from "react";

import { submitSupport, type SupportBody } from "./api";

type Status = "idle" | "sending" | "ok" | "fallback";

function useHelpForm() {
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [subject, setSubject] = React.useState("");
  const [message, setMessage] = React.useState("");
  const [tenant, setTenant] = React.useState("");
  const [status, setStatus] = React.useState<Status>("idle");
  const [mailtoHref, setMailtoHref] = React.useState("");
  const [err, setErr] = React.useState("");

  const submit = async () => {
    setErr("");
    if (!name.trim()) return setErr("Please add your name.");
    if (!email.includes("@")) return setErr("Please add a valid email.");
    if (!subject.trim()) return setErr("Please add a subject.");
    if (!message.trim()) return setErr("Please tell us what's going on.");
    setStatus("sending");
    const body: SupportBody = {
      name: name.trim(),
      email: email.trim(),
      subject: subject.trim(),
      message: message.trim(),
      tenant: tenant.trim() || undefined,
    };
    const res = await submitSupport(body);
    if (res.ok) {
      setStatus("ok");
    } else {
      setMailtoHref(res.mailtoHref || "");
      setStatus("fallback");
    }
  };

  return {
    name, setName, email, setEmail, subject, setSubject, message, setMessage,
    tenant, setTenant, status, mailtoHref, err, submit,
  };
}

/** The form fields + the honest success / fallback states. Shared by both shells. */
function HelpFormBody({ f }: { f: ReturnType<typeof useHelpForm> }) {
  if (f.status === "ok") {
    return (
      <div data-testid="support-confirm" style={{ textAlign: "center", padding: "16px 0" }}>
        <p style={{ fontSize: 14, color: "var(--ink-2)", lineHeight: 1.55 }}>
          Thanks, we've got your message. A human will get back to you by email soon.
        </p>
      </div>
    );
  }
  if (f.status === "fallback") {
    return (
      <div data-testid="support-fallback" style={{ textAlign: "center", padding: "8px 0" }}>
        <p style={{ fontSize: 14, color: "var(--ink-2)", marginBottom: 14, lineHeight: 1.5 }}>
          We couldn't submit that just now. Send it to us by email and we'll get right back to you.
        </p>
        <a
          className="btn btn-primary btn-lg"
          data-testid="support-mailto"
          style={{ width: "100%" }}
          href={f.mailtoHref}
        >
          Email us instead
        </a>
      </div>
    );
  }
  return (
    <>
      <label htmlFor="support-name" style={labelStyle}>Your name</label>
      <input
        id="support-name"
        className="lp-input"
        data-testid="support-name"
        placeholder="Your name"
        value={f.name}
        onChange={(e) => f.setName(e.target.value)}
      />
      <label htmlFor="support-email" style={labelStyle}>Work email</label>
      <input
        id="support-email"
        className="lp-input"
        data-testid="support-email"
        type="email"
        placeholder="you@company.com"
        value={f.email}
        onChange={(e) => f.setEmail(e.target.value)}
      />
      <label htmlFor="support-workspace" style={labelStyle}>Workspace (optional)</label>
      <input
        id="support-workspace"
        className="lp-input"
        data-testid="support-workspace"
        placeholder="If you have an account, your workspace name"
        value={f.tenant}
        onChange={(e) => f.setTenant(e.target.value)}
      />
      <label htmlFor="support-subject" style={labelStyle}>Subject</label>
      <input
        id="support-subject"
        className="lp-input"
        data-testid="support-subject"
        placeholder="What do you need help with?"
        value={f.subject}
        onChange={(e) => f.setSubject(e.target.value)}
      />
      <label htmlFor="support-message" style={labelStyle}>Message</label>
      <textarea
        id="support-message"
        className="lp-input"
        data-testid="support-message"
        rows={5}
        placeholder="Tell us what's going on and we'll help."
        value={f.message}
        onChange={(e) => f.setMessage(e.target.value)}
        style={{ resize: "vertical", minHeight: 96 }}
      />
      {f.err && <p style={{ fontSize: 12.5, color: "var(--rose)", marginTop: 8 }}>{f.err}</p>}
      <button
        className="btn btn-primary btn-lg"
        data-testid="support-submit"
        style={{ width: "100%", marginTop: 14 }}
        disabled={f.status === "sending"}
        onClick={f.submit}
      >
        {f.status === "sending" ? "Sending…" : "Send message"}
      </button>
    </>
  );
}

const labelStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "var(--ink-3)",
  display: "block",
  margin: "12px 0 6px",
};

/** Modal entry point — used by the footer "Contact support" + help button. */
export function HelpDialog({ onClose }: { onClose: () => void }) {
  const f = useHelpForm();

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const heading =
    f.status === "ok" ? "Message sent" : f.status === "fallback" ? "One more step" : "Contact support";
  const sub =
    f.status === "ok"
      ? "We'll reply by email."
      : f.status === "fallback"
        ? ""
        : "Tell us what you need and we'll help.";

  return (
    <div className="lp-modal-scrim" onClick={onClose}>
      <div
        className="lp-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Contact support"
        data-testid="support-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="lp-modal-head">
          <div style={{ flex: 1 }}>
            <h3 style={{ fontSize: 19, fontWeight: 730, letterSpacing: "-.02em" }}>{heading}</h3>
            {sub && <p style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>{sub}</p>}
          </div>
          <button
            className="icon-btn"
            aria-label="Close"
            data-testid="support-close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <div className="lp-modal-body">
          <HelpFormBody f={f} />
        </div>
      </div>
    </div>
  );
}

/** Standalone page entry point — the ?view=help seam. */
export default function HelpPage() {
  const f = useHelpForm();
  return (
    <main
      style={{
        maxWidth: 560,
        margin: "0 auto",
        padding: "56px 22px 80px",
        fontFamily: "system-ui, sans-serif",
      }}
      data-testid="support-page"
    >
      <p style={{ fontSize: 12, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--ink-4, #8a8278)", fontWeight: 650 }}>
        Help
      </p>
      <h1 style={{ fontSize: 28, fontWeight: 760, letterSpacing: "-.02em", margin: "6px 0 8px" }}>
        Contact support
      </h1>
      <p style={{ fontSize: 14.5, color: "var(--ink-3, #6b6258)", lineHeight: 1.6, marginBottom: 8 }}>
        Send us a message and a human will get back to you by email. If you're checking whether
        something is down, the <a href="/?view=status" style={{ color: "var(--accent-ink, #b4541f)" }}>status page</a> shows current health.
      </p>
      <div style={{ marginTop: 16 }}>
        <HelpFormBody f={f} />
      </div>
      <p style={{ marginTop: 22 }}>
        <a href="/" style={{ fontSize: 13, color: "var(--ink-4, #8a8278)" }}>Back to home</a>
      </p>
    </main>
  );
}
