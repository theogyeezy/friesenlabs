// SignupFlow: the marketing -> signup -> verify -> pay -> provisioned funnel.
//
// Multi-step flow wired to the signup API via ApiClient (mock mode for tests, so
// it runs fully offline). Steps:
//   1. account      email + password (+ strength meter) + phone
//   2. email-verify enter the emailed token
//   3. phone-verify enter the SMS OTP code
//   4. plan         choose a plan + EXPLICIT price consent ("You'll be charged $X/mo")
//   5. provisioning poll GET /signup until state === "active"
//   6. success      done; route to login
//
// TRUST + PRIVACY RULES:
//   - The password never leaves the browser: the API contract takes only
//     {email, phone}; the password input is local-only and is NEVER sent, logged,
//     or captured into analytics. The meter reads its length/variety in memory.
//   - The verify token and OTP code go to their endpoints and are never rendered
//     back, stored, or captured.
//   - The client never sends a tenant_id (see client.ts trust rule).
//   - Analytics is a no-op in mock/test mode and captures only coarse, non-secret
//     funnel events.

import React from "react";
import { ApiClient, defaultClient, friendlyErrorMessage, type SignupState } from "../api/client";
import { Analytics, defaultAnalytics } from "../analytics/posthog";
import { isAuthEnabled, signIn } from "../auth/cognito";

const { useState, useCallback, useRef, useEffect } = React;

// --- plans -----------------------------------------------------------------

interface Plan {
  id: string;
  name: string;
  pricePerMonth: number;
  blurb: string;
}

const PLANS: Plan[] = [
  { id: "starter", name: "Starter", pricePerMonth: 49, blurb: "One Managed agent, core CRM, Greenlight review." },
  { id: "team", name: "Team", pricePerMonth: 149, blurb: "Up to five Managed agents, Sidecar suite, shared inbox." },
  { id: "scale", name: "Scale", pricePerMonth: 399, blurb: "Unlimited agents, Cortex intelligence, priority support." },
];

// --- password strength (simple, zxcvbn-style, in-memory only) ---------------

interface Strength {
  score: 0 | 1 | 2 | 3 | 4;
  label: string;
}

// Coarse heuristic: never logs or transmits the value. Reads only derived signal.
function scorePassword(pw: string): Strength {
  if (!pw) return { score: 0, label: "Empty" };
  let score = 0;
  if (pw.length >= 8) score += 1;
  if (pw.length >= 12) score += 1;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score += 1;
  if (/\d/.test(pw)) score += 1;
  if (/[^A-Za-z0-9]/.test(pw)) score += 1;
  // Common-pattern penalty.
  if (/^(?:password|qwerty|12345|letmein)/i.test(pw)) score = Math.min(score, 1);
  const clamped = Math.min(4, score) as 0 | 1 | 2 | 3 | 4;
  const labels = ["Very weak", "Weak", "Fair", "Good", "Strong"];
  return { score: clamped, label: labels[clamped] };
}

const STRENGTH_COLORS = ["#b4413b", "#b4413b", "#c98a2b", "#3a8f5b", "#2f7d4f"];

// --- styling (self-contained inline; reuses brand CSS vars with fallbacks) --

const wrap: React.CSSProperties = {
  maxWidth: 460,
  margin: "0 auto",
  padding: "40px 24px",
  fontFamily: "var(--sans, system-ui, sans-serif)",
  color: "var(--ink, #2a2622)",
};

const card: React.CSSProperties = {
  border: "1px solid var(--line, #e3ddd3)",
  background: "var(--surface, #fff)",
  borderRadius: 16,
  padding: "28px 26px",
  boxShadow: "var(--shadow-md, 0 6px 24px rgba(0,0,0,.06))",
};

const label: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  fontWeight: 600,
  color: "var(--ink-3, #8a8278)",
  margin: "14px 0 6px",
};

const input: React.CSSProperties = {
  width: "100%",
  borderRadius: 10,
  border: "1px solid var(--line, #e3ddd3)",
  padding: "10px 12px",
  fontSize: 14,
  fontFamily: "inherit",
  boxSizing: "border-box",
  background: "var(--surface, #fff)",
  color: "var(--ink, #2a2622)",
};

const primaryBtn: React.CSSProperties = {
  width: "100%",
  marginTop: 20,
  padding: "11px 16px",
  borderRadius: 10,
  border: "none",
  background: "var(--accent, #2a2622)",
  color: "var(--accent-ink, #fff)",
  fontSize: 14,
  fontWeight: 680,
  cursor: "pointer",
};

const stepLabel: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  letterSpacing: ".06em",
  textTransform: "uppercase",
  color: "var(--ink-3, #8a8278)",
};

const titleStyle: React.CSSProperties = {
  fontSize: 24,
  fontWeight: 760,
  letterSpacing: "-.02em",
  margin: "6px 0 4px",
};

const subStyle: React.CSSProperties = {
  color: "var(--ink-3, #8a8278)",
  fontSize: 13.5,
  lineHeight: 1.5,
  marginBottom: 8,
};

const errStyle: React.CSSProperties = {
  color: "var(--rose, #b4413b)",
  fontSize: 13,
  marginTop: 12,
};

// --- the flow --------------------------------------------------------------

type Step = "account" | "email" | "phone" | "plan" | "provisioning" | "success";

export interface SignupFlowProps {
  client?: ApiClient;
  analytics?: Analytics;
  /** Override the provisioning poll interval (ms). Short in tests. */
  pollMs?: number;
}

export function SignupFlow({ client, analytics, pollMs = 600 }: SignupFlowProps) {
  const api = client ?? defaultClient();
  const ph = analytics ?? defaultAnalytics();

  const [step, setStep] = useState<Step>("account");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Account form. NOTE: password lives only in component state; it is never sent
  // to the API and never passed to analytics.
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");

  // Verify inputs (transient, never rendered back or captured).
  const [emailToken, setEmailToken] = useState("");
  const [phoneCode, setPhoneCode] = useState("");

  const [accountId, setAccountId] = useState<string | null>(null);
  const [plan, setPlan] = useState<Plan>(PLANS[1]);

  const strength = scorePassword(password);

  // Fire landing_view + init analytics once on mount.
  useEffect(() => {
    ph.init();
    ph.capture("landing_view", { surface: "signup" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submitAccount = useCallback(async () => {
    setError(null);
    if (!email.includes("@")) return setError("Enter a valid email address.");
    if (strength.score < 2) return setError("Choose a stronger password.");
    if (phone.replace(/\D/g, "").length < 7) return setError("Enter a valid phone number.");
    setBusy(true);
    try {
      // Contract sends {email, phone} only. Password stays in the browser.
      const res = await api.signup({ email, phone });
      setAccountId(res.account_id);
      ph.capture("signup_started", { surface: "signup" });
      setStep("email");
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't start signup. Please try again."));
    } finally {
      setBusy(false);
    }
  }, [api, ph, email, phone, strength.score]);

  const submitEmail = useCallback(async () => {
    if (!accountId) return;
    setError(null);
    if (!emailToken.trim()) return setError("Enter the code we emailed you.");
    setBusy(true);
    try {
      await api.verifyEmail(accountId, { token: emailToken.trim() });
      setEmailToken("");
      ph.capture("email_verified");
      setStep("phone");
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't verify that code. Check it and try again."));
    } finally {
      setBusy(false);
    }
  }, [api, ph, accountId, emailToken]);

  const submitPhone = useCallback(async () => {
    if (!accountId) return;
    setError(null);
    if (!phoneCode.trim()) return setError("Enter the code we texted you.");
    setBusy(true);
    try {
      await api.verifyPhone(accountId, { code: phoneCode.trim() });
      setPhoneCode("");
      ph.capture("phone_verified");
      setStep("plan");
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't verify that code. Check it and try again."));
    } finally {
      setBusy(false);
    }
  }, [api, ph, accountId, phoneCode]);

  const submitCheckout = useCallback(async () => {
    if (!accountId) return;
    setError(null);
    setBusy(true);
    ph.capture("payment_submitted", { plan: plan.id });
    try {
      await api.checkout(accountId, { plan: plan.id });
      // Payment succeeded; the client does NOT emit a revenue amount (that is
      // captured server-side on the Stripe webhook).
      ph.capture("payment_succeeded", { plan: plan.id });
      setStep("provisioning");
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't start checkout. You haven't been charged — please try again."));
      setBusy(false);
    }
  }, [api, ph, accountId, plan]);

  // Provisioning poll: GET /signup until state === "active", then success.
  const pollRef = useRef<number | null>(null);
  useEffect(() => {
    if (step !== "provisioning" || !accountId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const { state } = await api.getSignup(accountId);
        if (cancelled) return;
        if (state === ("active" as SignupState)) {
          ph.capture("instance_provisioned");
          setBusy(false);
          setStep("success");
          return;
        }
        pollRef.current = window.setTimeout(tick, pollMs);
      } catch (e) {
        if (cancelled) return;
        setError(friendlyErrorMessage(e, "We couldn't check on your workspace. Refresh to keep watching — setup continues either way."));
        setBusy(false);
      }
    };
    void tick();

    return () => {
      cancelled = true;
      if (pollRef.current !== null) window.clearTimeout(pollRef.current);
    };
  }, [step, accountId, api, ph, pollMs]);

  const goToLogin = useCallback(() => {
    ph.capture("first_login");
    if (isAuthEnabled()) {
      // Real builds route to the Cognito Hosted UI sign-in.
      void signIn();
      return;
    }
    // Mock/unconfigured builds keep the demo behavior.
    window.location.search = "?view=greenlight";
  }, [ph]);

  return (
    <div style={wrap} data-testid="signup-flow" data-step={step}>
      <div style={{ marginBottom: 16, textAlign: "center" }}>
        <div style={stepLabel}>Get started with Friesen Labs</div>
      </div>

      <div style={card}>
        {step === "account" && (
          <div data-testid="step-account">
            <h1 style={titleStyle}>Create your workspace</h1>
            <p style={subStyle}>Your Managed agents run the busywork. Set up takes a minute.</p>

            <label style={label} htmlFor="su-email">Work email</label>
            <input
              id="su-email"
              data-testid="su-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={input}
            />

            <label style={label} htmlFor="su-password">Password</label>
            <input
              id="su-password"
              data-testid="su-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={input}
            />
            {password.length > 0 && (
              <div data-testid="pw-meter" style={{ marginTop: 8 }}>
                <div style={{ display: "flex", gap: 4 }}>
                  {[0, 1, 2, 3].map((i) => (
                    <div
                      key={i}
                      style={{
                        flex: 1,
                        height: 5,
                        borderRadius: 3,
                        background:
                          i < strength.score
                            ? STRENGTH_COLORS[strength.score]
                            : "var(--line, #e3ddd3)",
                      }}
                    />
                  ))}
                </div>
                <div
                  data-testid="pw-strength-label"
                  style={{ fontSize: 12, marginTop: 4, color: "var(--ink-3, #8a8278)" }}
                >
                  Password strength: {strength.label}
                </div>
              </div>
            )}

            <label style={label} htmlFor="su-phone">Mobile phone</label>
            <input
              id="su-phone"
              data-testid="su-phone"
              type="tel"
              autoComplete="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              style={input}
            />

            <button data-testid="account-submit" style={primaryBtn} disabled={busy} onClick={() => void submitAccount()}>
              {busy ? "Creating..." : "Continue"}
            </button>
          </div>
        )}

        {step === "email" && (
          <div data-testid="step-email">
            <h1 style={titleStyle}>Verify your email</h1>
            <p style={subStyle}>We sent a code to {email}. Enter it to confirm.</p>
            <label style={label} htmlFor="su-email-token">Email code</label>
            <input
              id="su-email-token"
              data-testid="su-email-token"
              inputMode="numeric"
              value={emailToken}
              onChange={(e) => setEmailToken(e.target.value)}
              style={input}
            />
            <button data-testid="email-submit" style={primaryBtn} disabled={busy} onClick={() => void submitEmail()}>
              {busy ? "Verifying..." : "Verify email"}
            </button>
          </div>
        )}

        {step === "phone" && (
          <div data-testid="step-phone">
            <h1 style={titleStyle}>Verify your phone</h1>
            <p style={subStyle}>We texted a one time code to your phone. Enter it below.</p>
            <label style={label} htmlFor="su-phone-code">SMS code</label>
            <input
              id="su-phone-code"
              data-testid="su-phone-code"
              inputMode="numeric"
              value={phoneCode}
              onChange={(e) => setPhoneCode(e.target.value)}
              style={input}
            />
            <button data-testid="phone-submit" style={primaryBtn} disabled={busy} onClick={() => void submitPhone()}>
              {busy ? "Verifying..." : "Verify phone"}
            </button>
          </div>
        )}

        {step === "plan" && (
          <div data-testid="step-plan">
            <h1 style={titleStyle}>Choose a plan</h1>
            <p style={subStyle}>Switch or cancel anytime from billing.</p>

            <div style={{ display: "grid", gap: 10, marginTop: 8 }}>
              {PLANS.map((p) => {
                const selected = p.id === plan.id;
                return (
                  <button
                    key={p.id}
                    data-testid={`plan-${p.id}`}
                    onClick={() => setPlan(p)}
                    style={{
                      textAlign: "left",
                      border: selected
                        ? "2px solid var(--accent, #2a2622)"
                        : "1px solid var(--line, #e3ddd3)",
                      background: selected ? "var(--accent-soft, #f4f1ea)" : "var(--surface, #fff)",
                      borderRadius: 12,
                      padding: "12px 14px",
                      cursor: "pointer",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                      <span style={{ fontWeight: 700, fontSize: 15 }}>{p.name}</span>
                      <span style={{ fontWeight: 700, fontSize: 15 }}>${p.pricePerMonth}/mo</span>
                    </div>
                    <div style={{ fontSize: 12.5, color: "var(--ink-3, #8a8278)", marginTop: 3 }}>{p.blurb}</div>
                  </button>
                );
              })}
            </div>

            {/* Explicit price consent: shown BEFORE the pay action. */}
            <div
              data-testid="price-consent"
              style={{
                marginTop: 16,
                padding: "12px 14px",
                borderRadius: 12,
                background: "var(--accent-soft, #f4f1ea)",
                border: "1px solid var(--line, #e3ddd3)",
                fontSize: 13.5,
                lineHeight: 1.5,
              }}
            >
              You'll be charged{" "}
              <b data-testid="price-consent-amount">${plan.pricePerMonth}/mo</b> for the {plan.name} plan.
              Billing starts today and renews monthly until you cancel.
            </div>

            <button data-testid="pay-submit" style={primaryBtn} disabled={busy} onClick={() => void submitCheckout()}>
              {busy ? "Starting checkout..." : `Pay $${plan.pricePerMonth}/mo and continue`}
            </button>
          </div>
        )}

        {step === "provisioning" && (
          <div data-testid="step-provisioning" style={{ textAlign: "center", padding: "16px 0" }}>
            <h1 style={titleStyle}>Provisioning your workspace</h1>
            <p style={subStyle}>
              We're spinning up your Managed runtime and isolated workspace. This takes a moment.
            </p>
            <div
              aria-label="provisioning"
              style={{
                width: 28,
                height: 28,
                margin: "18px auto 0",
                borderRadius: "50%",
                border: "3px solid var(--line, #e3ddd3)",
                borderTopColor: "var(--accent, #2a2622)",
                animation: "spin 0.8s linear infinite",
              }}
            />
            <style>{"@keyframes spin{to{transform:rotate(360deg)}}"}</style>
          </div>
        )}

        {step === "success" && (
          <div data-testid="step-success" style={{ textAlign: "center" }}>
            <h1 style={titleStyle}>You're all set</h1>
            <p style={subStyle}>
              Your workspace is live. Sign in to meet your Managed agents and start clearing your queue.
            </p>
            <button data-testid="go-login" style={primaryBtn} onClick={goToLogin}>
              Go to sign in
            </button>
          </div>
        )}

        {error && <div data-testid="signup-error" style={errStyle}>{error}</div>}
      </div>
    </div>
  );
}

export default SignupFlow;
