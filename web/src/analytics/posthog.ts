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
  | "checkout_started"
  | "payment_submitted"
  | "payment_succeeded"
  | "instance_provisioned"
  | "first_login"
  | "chat_message_sent"
  // Balto (NL view creation): coarse outcome signals only — never the ask text.
  | "view_synthesis_finished"
  | "view_synthesis_saved";

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
  /** First-party reverse-proxy origin for INGESTION (same-origin path). */
  apiHost?: string;
  /** Where the PostHog app/assets (toolbar) load from — distinct from apiHost.
   *  A reverse proxy forwards ingestion but the UI host must point at the real
   *  PostHog app, so reusing apiHost here breaks the toolbar. */
  uiHost?: string;
  /** Inject a PostHog-like impl. When omitted in a real build, the wrapper
   *  lazy-loads `posthog-js` on init() (a separate chunk, kept off the landing's
   *  first load). Tests inject a recording stub so nothing is ever loaded. */
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
    // Safe defaults: an absent key leaves analytics a hard no-op (see the
    // disabled rule below). VITE_POSTHOG_KEY must be injected at build time on
    // the Amplify app for ingestion to turn on in production.
    key: env.VITE_POSTHOG_KEY ?? "",
    disabled: mock,
    // First-party proxy path for INGESTION; see web/README.md ("/ph reverse
    // proxy"). The browser only ever talks to our own origin. Resolved to an
    // absolute URL in init() (posthog-js wants a full origin, not a bare path).
    apiHost: env.VITE_POSTHOG_HOST ?? "/ph",
    // The PostHog app host for the toolbar/assets — NOT the ingestion proxy.
    // Defaults to PostHog US cloud; override per-region with VITE_POSTHOG_UI_HOST.
    uiHost: env.VITE_POSTHOG_UI_HOST ?? "https://us.posthog.com",
  };
}

/** A capture buffered before the lazily-loaded SDK is ready. Bounded so a
 *  never-arriving impl can't grow memory without bound. */
interface PendingCapture {
  event: FunnelEvent;
  props?: EventProps;
}
const MAX_QUEUE = 50;

export class Analytics {
  private readonly key: string;
  private readonly disabled: boolean;
  private readonly apiHost: string;
  private readonly uiHost: string;
  private impl: PostHogLike | null;
  private started = false;
  private loading = false;
  // Events captured before the SDK finished loading; flushed on attach.
  private queue: PendingCapture[] = [];

  constructor(config: AnalyticsConfig = {}) {
    this.key = config.key ?? "";
    this.apiHost = config.apiHost ?? "/ph";
    this.uiHost = config.uiHost ?? "https://us.posthog.com";
    this.impl = config.impl ?? null;
    // Hard no-op if explicitly disabled OR if there is no key. Either is enough.
    this.disabled = Boolean(config.disabled) || this.key === "";
  }

  /** True when this instance will never make a network call. */
  isEnabled(): boolean {
    return !this.disabled;
  }

  /**
   * Provide a PostHog-like impl (the real SDK or a test stub). Initializes it
   * and flushes any queued events. Tests call this directly to inject a
   * recording stub; in a real build init() lazy-loads `posthog-js` and calls
   * this for you.
   */
  attach(impl: PostHogLike): void {
    this.impl = impl;
    if (!this.disabled) {
      this.startImpl();
      this.flush();
    }
  }

  /** Run the underlying SDK's init exactly once. */
  private startImpl(): void {
    if (!this.impl || this.started) return;
    this.started = true;
    // Resolve a bare proxy path ("/ph") to an absolute same-origin URL — the
    // SDK wants a full origin for ingestion, and a relative path silently
    // breaks it. SSR-safe: fall back to the raw value when there's no window.
    const apiHost =
      /^https?:\/\//.test(this.apiHost) || typeof window === "undefined"
        ? this.apiHost
        : `${window.location.origin}${this.apiHost.startsWith("/") ? "" : "/"}${this.apiHost}`;
    this.impl.init(this.key, {
      api_host: apiHost,
      // The toolbar/app host is the REAL PostHog app, not the ingestion proxy.
      ui_host: this.uiHost,
      // Mask every input in session replay so no password / OTP / PII is captured.
      session_recording: { maskAllInputs: true },
      mask_all_text: false,
      // We call capture() explicitly at each funnel step.
      capture_pageview: false,
      autocapture: false,
      persistence: "localStorage",
    });
  }

  /** Flush queued events through the now-ready impl. */
  private flush(): void {
    if (!this.impl) return;
    const pending = this.queue;
    this.queue = [];
    for (const c of pending) this.impl.capture(c.event, sanitize(c.props));
  }

  /**
   * Initialize analytics. No-op when disabled. When no impl is attached (the
   * normal real build), lazy-loads `posthog-js` as a separate chunk so it never
   * weighs down the landing's first load, then attaches + flushes. Idempotent.
   */
  init(): void {
    if (this.disabled) return;
    if (this.impl) {
      this.startImpl();
      this.flush();
      return;
    }
    if (this.loading) return;
    this.loading = true;
    // Dynamic import => its own chunk, only fetched when analytics is actually
    // enabled (real mode + a key present). Failure is silent: a missing/blocked
    // SDK must never break the app or surface an error to the user.
    void import("posthog-js")
      .then((mod) => {
        const ph = (mod.default ?? mod) as unknown as PostHogLike;
        this.attach(ph);
      })
      .catch(() => {
        this.loading = false;
      });
  }

  /**
   * Capture a funnel event. No-op when disabled. Buffers (bounded) until the
   * SDK is ready so events fired during the async load aren't lost. Strips any
   * forbidden keys (tenant_id, password, token, code) defensively so a caller
   * mistake can never leak a secret into analytics.
   */
  capture(event: FunnelEvent, props?: EventProps): void {
    if (this.disabled) return;
    if (this.impl) {
      this.impl.capture(event, sanitize(props));
      return;
    }
    // Kick off the lazy load on the first capture if init() hasn't run yet.
    if (!this.loading) this.init();
    if (this.queue.length < MAX_QUEUE) this.queue.push({ event, props });
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
