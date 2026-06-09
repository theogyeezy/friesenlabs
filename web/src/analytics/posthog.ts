// Thin, injectable PostHog wrapper for the Uplift web app.
//
// DESIGN RULES (load-bearing):
//   1. No real key in code. The project key is read ONLY from the environment
//      (`import.meta.env.VITE_POSTHOG_KEY`); there is no literal key anywhere in
//      the source.
//   2. NO-OP in mock/test mode and whenever no key is present. Playwright runs
//      in mock mode, so analytics make zero network calls and the funnel is
//      fully offline-safe. Without a key the wrapper silently does nothing.
//   3. Session replay masks every input (`maskAllInputs: true`), so passwords,
//      OTP codes, emails, and phone numbers never reach a recording.
//   4. The client never captures tenant_id, passwords, or tokens. Capture sites
//      pass only coarse, non-secret properties (e.g. a plan id).
//   5. First-party reverse proxy: events are sent to a same-origin `/ph` path
//      (documented in web/README.md) so a third-party domain is never contacted
//      directly from the browser. The proxy forwards to PostHog server-side.

// The canonical funnel events. Revenue is captured SERVER-SIDE on the Stripe
// webhook, so the client deliberately does NOT emit any `$`-amount on
// `payment_succeeded`; it only marks the step.
export type FunnelEvent =
  | "landing_view"
  | "signup_started"
  | "email_verified"
  | "phone_verified"
  | "payment_submitted"
  | "payment_succeeded"
  | "instance_provisioned"
  | "first_login";

/** Non-secret, coarse properties only. Never a password, token, or tenant_id. */
export type EventProps = Record<string, string | number | boolean | null | undefined>;

/** The minimal surface of the real PostHog SDK we depend on. Injectable for tests. */
export interface PostHogLike {
  init(key: string, config: Record<string, unknown>): void;
  capture(event: string, props?: EventProps): void;
  reset?(): void;
}

export interface AnalyticsConfig {
  /** PostHog project key. Read from env only; empty/undefined => no-op. */
  key?: string;
  /** When true (mock/test), the wrapper is a hard no-op. */
  disabled?: boolean;
  /** First-party reverse-proxy origin for ingestion (same-origin path). */
  apiHost?: string;
  /** Inject a PostHog-like impl. When omitted in a real build, capture is a no-op
   *  until `attach()` provides one. Tests inject a recording stub. */
  impl?: PostHogLike;
}

/**
 * Resolve analytics config from the Vite environment.
 *
 * Mirrors the API client's env handling. Mock mode is detected the same way
 * (VITE_API_MOCK), so analytics are disabled wherever the API is mocked, i.e.
 * in Playwright and local previews.
 */
export function analyticsConfigFromEnv(): AnalyticsConfig {
  const env =
    (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};
  const mockFlag = env.VITE_API_MOCK;
  // Mock unless explicitly disabled with "0" / "false" (same rule as the API client).
  const mock = mockFlag === undefined ? true : !(mockFlag === "0" || mockFlag === "false");
  return {
    key: env.VITE_POSTHOG_KEY ?? "",
    disabled: mock,
    // First-party proxy path; see web/README.md ("/ph reverse proxy"). The
    // browser only ever talks to our own origin.
    apiHost: env.VITE_POSTHOG_HOST ?? "/ph",
  };
}

export class Analytics {
  private readonly key: string;
  private readonly disabled: boolean;
  private readonly apiHost: string;
  private impl: PostHogLike | null;
  private started = false;

  constructor(config: AnalyticsConfig = {}) {
    this.key = config.key ?? "";
    this.apiHost = config.apiHost ?? "/ph";
    this.impl = config.impl ?? null;
    // Hard no-op if explicitly disabled OR if there is no key. Either is enough.
    this.disabled = Boolean(config.disabled) || this.key === "";
  }

  /** True when this instance will never make a network call. */
  isEnabled(): boolean {
    return !this.disabled;
  }

  /**
   * Provide the real PostHog SDK (or a test stub) after construction. In a real
   * build, app code would `import posthog from "posthog-js"` and call
   * `analytics.attach(posthog)`; we keep the dependency out of the offline build.
   */
  attach(impl: PostHogLike): void {
    this.impl = impl;
  }

  /** Initialize the SDK once. No-op when disabled or no impl is attached. */
  init(): void {
    if (this.disabled || this.started || !this.impl) return;
    this.started = true;
    this.impl.init(this.key, {
      api_host: this.apiHost,
      // Route asset loads through the same first-party proxy.
      ui_host: this.apiHost,
      // Mask every input in session replay so no password / OTP / PII is captured.
      session_recording: { maskAllInputs: true },
      mask_all_text: false,
      // We call capture() explicitly at each funnel step.
      capture_pageview: false,
      autocapture: false,
      persistence: "localStorage",
    });
  }

  /**
   * Capture a funnel event. No-op when disabled. Strips any forbidden keys
   * (tenant_id, password, token, code) defensively so a caller mistake can never
   * leak a secret into analytics.
   */
  capture(event: FunnelEvent, props?: EventProps): void {
    if (this.disabled || !this.impl) return;
    this.impl.capture(event, sanitize(props));
  }

  /** Clear identity on logout. No-op when disabled. */
  reset(): void {
    if (this.disabled || !this.impl) return;
    this.impl.reset?.();
  }
}

// Defensive allowlist-by-denial: never let these reach an analytics payload even
// if a caller passes them by mistake. tenant_id is forbidden everywhere.
const FORBIDDEN = new Set([
  "tenant_id",
  "tenantId",
  "password",
  "pass",
  "token",
  "code",
  "otp",
  "secret",
]);

function sanitize(props?: EventProps): EventProps | undefined {
  if (!props) return props;
  const out: EventProps = {};
  for (const [k, v] of Object.entries(props)) {
    if (FORBIDDEN.has(k)) continue;
    out[k] = v;
  }
  return out;
}

/** Build an Analytics instance from the Vite environment (disabled in mock/test). */
export function createAnalytics(overrides: AnalyticsConfig = {}): Analytics {
  return new Analytics({ ...analyticsConfigFromEnv(), ...overrides });
}

/** A shared, lazily-created default analytics instance for app surfaces. */
let _default: Analytics | null = null;
export function defaultAnalytics(): Analytics {
  if (_default === null) _default = createAnalytics();
  return _default;
}
