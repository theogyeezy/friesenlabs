// SignupFlow: the marketing -> signup -> verify -> pay -> provisioned funnel.
//
// Multi-step flow wired to the signup API via ApiClient (mock mode for tests, so
// it runs fully offline). Steps:
//   1. account      email + password (+ strength meter) + phone
//   2. email-verify enter the emailed token
//   3. phone-verify enter the SMS OTP code
//   4. plan         choose a plan + EXPLICIT price consent ("You'll be charged $X/mo")
//   5. checkout     POST /signup/{id}/checkout answers {checkout_url}: the browser
//                   is SENT THERE (window.location.assign) — Stripe's hosted page
//                   takes the card. The env-gated internal bypass instead answers
//                   {checkout_url: null, bypass: "internal_comp"} (settled
//                   server-side through the same idempotent path as the webhook),
//                   which advances straight to provisioning. The client NEVER
//                   claims payment success on its own: success is only ever the
//                   server's word via the status poll below.
//   5b. resume      the Stripe round-trip unmounts the SPA, so the account id is
//                   parked in sessionStorage before the redirect; on return the
//                   flow resumes from it and asks GET /signup/{id} where the
//                   funnel honestly is (the signed webhook — the ONLY provisioning
//                   trigger — may lag the browser redirect).
//   6. provisioning poll GET /signup until state === "active"
//   7. success      done; route to login
//
// TRUST + PRIVACY RULES:
//   - The password is sent exactly once — in the POST /signup body, over HTTPS — so the
//     server can set it as the user's permanent Cognito credential. It is NEVER logged,
//     stored in the DB, or echoed in any response. The strength meter reads only derived
//     signal (length/variety) in memory; after submission the state is zeroed.
//   - The verify token and OTP code go to their endpoints and are never rendered
//     back, stored, or captured.
//   - The client never sends a tenant_id (see client.ts trust rule).
//   - Analytics is a no-op in mock/test mode and captures only coarse, non-secret
//     funnel events.

import React from "react";
import { ApiClient, defaultClient, friendlyErrorMessage, type SignupState } from "../api/client";
import { Analytics, defaultAnalytics } from "../analytics/posthog";
import { isAuthEnabled, signIn } from "../auth/cognito";
import { PLAN_TIERS, formatMonthlyPrice, type PlanTier } from "../pricing";
import { TurnstileWidget } from "./turnstile";

const { useState, useCallback, useRef, useEffect } = React;

// --- plans -----------------------------------------------------------------
// The plan tiers + prices come from the single pricing source of truth
// (../pricing): one place owns 99/299/799, so the cards, the consent line, and
// the pay button can never drift apart.

type Plan = PlanTier;

const PLANS: readonly Plan[] = PLAN_TIERS;

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

// --- checkout round-trip marker ---------------------------------------------
// Stripe's hosted Checkout page is a full navigation away from the SPA, so all
// React state is lost. Before redirecting we park the OPAQUE account id (never
// a password, token, or tenant_id) in sessionStorage; on return the flow
// resumes from it and asks the server where the funnel honestly is.

const PENDING_CHECKOUT_KEY = "fl_signup_pending";

function readPendingCheckout(): string | null {
  try {
    return window.sessionStorage.getItem(PENDING_CHECKOUT_KEY);
  } catch {
    return null;
  }
}

function writePendingCheckout(accountId: string): void {
  try {
    window.sessionStorage.setItem(PENDING_CHECKOUT_KEY, accountId);
  } catch {
    // Storage unavailable: the redirect still works; resume degrades to a
    // fresh signup rather than blocking checkout.
  }
}

function clearPendingCheckout(): void {
  try {
    window.sessionStorage.removeItem(PENDING_CHECKOUT_KEY);
  } catch {
    // Best-effort cleanup only.
  }
}

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

  // Cloudflare Turnstile token (captcha seam). Stays null — and the widget
  // renders nothing, loads nothing — unless the build carries
  // VITE_TURNSTILE_SITE_KEY. When present, the latest token rides the
  // signup-start request as the x-captcha-token header.
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);

  const [accountId, setAccountId] = useState<string | null>(null);
  // SIGNUP_REQUIRE_PHONE feature flag (from the create response). When false, the phone-verify
  // step is skipped — email-only verification while SMS approval is pending. Defaults true.
  const [requirePhone, setRequirePhone] = useState(true);
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
      // Send email, phone, AND the user's chosen password over HTTPS. The server passes
      // the password directly to Cognito admin_set_user_password(Permanent=True) so first
      // login works with what was typed. The password is never logged, stored in the DB, or
      // echoed in any response. It is zeroed from component state after submission below.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await api.signup(
        { email, phone, password } as any,
        captchaToken ? { captchaToken } : undefined,
      );
      setAccountId(res.account_id);
      // The server tells us whether phone verification is required (feature flag). Only false
      // explicitly disables it; anything else keeps the phone step (safe default).
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setRequirePhone((res as any).require_phone !== false);
      setPassword(""); // zero out after submit — no longer needed in component state
      ph.capture("signup_started", { surface: "signup" });
      setStep("email");
    } catch (e) {
      // A missing/invalid captcha token comes back as the server's 400 detail
      // and surfaces here verbatim via friendlyErrorMessage (4xx detail rule).
      setError(friendlyErrorMessage(e, "Couldn't start signup. Please try again."));
    } finally {
      setBusy(false);
    }
  }, [api, ph, email, phone, strength.score, captchaToken]);

  const submitEmail = useCallback(async () => {
    if (!accountId) return;
    setError(null);
    if (!emailToken.trim()) return setError("Enter the code we emailed you.");
    setBusy(true);
    try {
      await api.verifyEmail(accountId, { token: emailToken.trim() });
      setEmailToken("");
      ph.capture("email_verified");
      // Skip the phone step when phone verification is flagged off (email-only launch).
      setStep(requirePhone ? "phone" : "plan");
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't verify that code. Check it and try again."));
    } finally {
      setBusy(false);
    }
  }, [api, ph, accountId, emailToken, requirePhone]);

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
      const res = await api.checkout(accountId, { plan: plan.id });
      if (res.checkout_url) {
        // Hand the browser to Stripe's hosted Checkout page. The client NEVER
        // claims payment success: provisioning fires only off the signed
        // Stripe webhook, and on return the flow resumes from the pending
        // marker and polls GET /signup/{id} for the real state. (No revenue
        // amount is emitted client-side either — that is captured server-side
        // on the webhook.)
        writePendingCheckout(accountId);
        ph.capture("checkout_redirected", { plan: plan.id });
        window.location.assign(res.checkout_url);
        return; // navigating away — leave the button in its busy state
      }
      if (res.bypass === "internal_comp") {
        // Env-gated internal-domain bypass: the server already settled the
        // payment through the SAME idempotent ledger + provisioning path as
        // the webhook (no Stripe page exists). Advance honestly and let the
        // status poll — not this client — decide when the workspace is live.
        writePendingCheckout(accountId);
        ph.capture("checkout_settled_internal", { plan: plan.id });
        setStep("provisioning");
        return;
      }
      // No hosted page and no settled bypass: checkout did not start.
      setError("Couldn't start checkout. You haven't been charged — please try again.");
      setBusy(false);
    } catch (e) {
      setError(friendlyErrorMessage(e, "Couldn't start checkout. You haven't been charged — please try again."));
      setBusy(false);
    }
  }, [api, ph, accountId, plan]);

  // Resume after the Stripe round-trip (the redirect unmounted the SPA). Runs
  // once on mount: when a pending-checkout marker exists, ask the SERVER where
  // the funnel honestly is (GET /signup/{id}) instead of trusting the redirect.
  useEffect(() => {
    const pending = readPendingCheckout();
    if (!pending) return;
    let cancelled = false;
    setAccountId(pending);
    setBusy(true);
    void (async () => {
      const returnedAs = new URLSearchParams(window.location.search).get("checkout");
      try {
        const { state } = await api.getSignup(pending);
        if (cancelled) return;
        if (state === "paid" || state === "provisioning" || state === "active") {
          // Payment is the server's word now — keep polling to completion.
          setStep("provisioning");
          return;
        }
        if (returnedAs === "success") {
          // Stripe redirected back as paid, but the signed webhook (the ONLY
          // provisioning trigger) can lag the browser. Keep polling rather
          // than calling it failed — the poll advances the moment it lands.
          setStep("provisioning");
          return;
        }
        // Cancelled / abandoned checkout: nothing was charged. Land back on
        // the plan step honestly so the user can pay when ready.
        clearPendingCheckout();
        setBusy(false);
        setStep("plan");
        if (returnedAs === "cancel") {
          setError("Checkout was cancelled. You haven't been charged — pick a plan when you're ready.");
        }
      } catch (e) {
        if (cancelled) return;
        clearPendingCheckout();
        setBusy(false);
        setStep("plan");
        setError(friendlyErrorMessage(e, "We couldn't check your payment status. Refresh to try again — you won't be charged twice."));
      }
    })();
    return () => {
      cancelled = true;
    };
    // Mount-only by design: the marker is a cross-navigation handoff.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          // The round-trip marker has served its purpose (a refresh mid-poll
          // resumes via it; a finished signup must not).
          clearPendingCheckout();
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

            {/* Captcha seam: renders null (no script, no header) without a site key. */}
            <TurnstileWidget onToken={setCaptchaToken} />

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
                      <span style={{ fontWeight: 700, fontSize: 15 }}>{formatMonthlyPrice(p.pricePerMonth)}</span>
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
              <b data-testid="price-consent-amount">{formatMonthlyPrice(plan.pricePerMonth)}</b> for the {plan.name} plan.
              Billing starts today and renews monthly until you cancel.
            </div>

            <button data-testid="pay-submit" style={primaryBtn} disabled={busy} onClick={() => void submitCheckout()}>
              {busy ? "Starting checkout..." : `Pay ${formatMonthlyPrice(plan.pricePerMonth)} and continue`}
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
