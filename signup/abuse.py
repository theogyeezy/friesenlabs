"""Acquisition-funnel abuse / spam controls (pre-auth, IP-keyed).

So one bad actor can't run up the Anthropic bill or spam signup/leads. THREE checks, all
pre-tenant + unauthenticated by design (an attacker has no account/tenant yet):

  1. ``DisposableEmailBlocklist`` — reject obvious throwaway/disposable email domains at
     signup-start. Backed by a maintained static data file (``disposable_email_domains.txt``),
     OVERRIDABLE via env (alternate file + an extra comma-separated set). Honest copy, never a
     silent drop.

  2. ``SignupVelocityLimiter`` — a fixed-window in-process per-IP counter capping signups +
     verification-resends per IP per window (429 on exceed). It MIRRORS — never shares — the
     leads endpoint's ``_IpRateLimiter`` shape and the SAME trusted-IP parse
     (``api.public_routes._trusted_client_ip``): the rate-limit key is the viewer IP at the
     CloudFront→ALB trust boundary, never the spoofable left of X-Forwarded-For nor the shared
     ALB socket peer. In-process is honest scope (per Fargate task → effective ceiling is
     N×limit); the CloudFront WAF rate rule remains the real flood gate. This is the
     ACQUISITION-scoped twin of the tenant-limits lane's POST-AUTH, tenant-keyed limiter — they
     deliberately do NOT share a module.

  3. ``CaptchaVerifier`` — a SEAM where a CAPTCHA / Turnstile token COULD be required at
     signup-start, **no-op (defaults OPEN) by default**. Nothing real is integrated here; this
     just gives a single, well-typed place to later wire Cloudflare Turnstile / hCaptcha / reCAPTCHA
     verification without touching the route. See the PR body for the wiring plan.

All three are constructed in api/prod_deps.py and injected into the signup routes; tests build them
directly. NONE of them reach the network in this module.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable

# The shipped static blocklist (signup/disposable_email_domains.txt — next to this module).
_DEFAULT_BLOCKLIST_PATH = Path(__file__).resolve().parent / "disposable_email_domains.txt"

# Env override names (mirrored into shared/config.py so that module stays the single source of
# truth for every env var the app reads — CONTRIBUTING.md §Env-var / secret-name contract).
ENV_DISPOSABLE_DOMAINS_FILE = "SIGNUP_DISPOSABLE_DOMAINS_FILE"   # alternate blocklist file path
ENV_DISPOSABLE_DOMAINS_EXTRA = "SIGNUP_DISPOSABLE_DOMAINS_EXTRA"  # comma-separated extra domains


def _parse_blocklist_text(text: str) -> set[str]:
    """One lowercase domain per line; '#'-comment lines and blanks ignored. No inline comments."""
    out: set[str] = set()
    for line in text.splitlines():
        line = line.strip().lower()
        if not line or line.startswith("#"):
            continue
        out.add(line)
    return out


def _parse_extra(raw: str) -> set[str]:
    """Comma-separated extra domains (the SIGNUP_DISPOSABLE_DOMAINS_EXTRA override)."""
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


class DisposableEmailBlocklist:
    """A normalized set of disposable email domains, with an honest-copy membership check.

    The default source is the shipped data file; an alternate file and/or an extra inline set
    can be layered on (the override knobs). Construction is the only place that touches the
    filesystem — ``is_disposable`` / ``check`` are pure and allocation-light.
    """

    def __init__(self, domains: set[str] | frozenset[str] | None = None):
        # Domains are stored lowercased; the empty set is a legitimate "nothing blocked" config.
        self._domains: frozenset[str] = frozenset(d.lower() for d in (domains or set()))

    @classmethod
    def from_file(cls, path: str | os.PathLike,
                  extra: set[str] | None = None) -> "DisposableEmailBlocklist":
        """Load a blocklist file (UTF-8). A missing/unreadable file degrades to the extra set only
        (never crash signup over a misconfigured override) — the caller decides whether to log."""
        domains: set[str] = set()
        try:
            domains = _parse_blocklist_text(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            domains = set()
        if extra:
            domains |= {d.lower() for d in extra}
        return cls(domains)

    @classmethod
    def from_env(cls) -> "DisposableEmailBlocklist":
        """Build from the shipped file, honoring the two override knobs:
          * SIGNUP_DISPOSABLE_DOMAINS_FILE  -> use that path instead of the shipped file
          * SIGNUP_DISPOSABLE_DOMAINS_EXTRA -> add these comma-separated domains on top
        """
        path = os.environ.get(ENV_DISPOSABLE_DOMAINS_FILE, "") or _DEFAULT_BLOCKLIST_PATH
        extra = _parse_extra(os.environ.get(ENV_DISPOSABLE_DOMAINS_EXTRA, ""))
        return cls.from_file(path, extra=extra)

    def __len__(self) -> int:
        return len(self._domains)

    def _domain_of(self, email: str) -> str:
        return (email or "").strip().lower().rsplit("@", 1)[-1]

    def is_disposable(self, email: str) -> bool:
        """True iff the email's domain is on the blocklist. Empty/junk -> False (the email-shape
        validator owns rejecting malformed addresses; this check only fires on a real domain)."""
        if not email or "@" not in email:
            return False
        return self._domain_of(email) in self._domains

    def check(self, email: str) -> None:
        """Raise DisposableEmailError (honest copy) when the domain is blocked; else return None."""
        if self.is_disposable(email):
            raise DisposableEmailError(self._domain_of(email))


class DisposableEmailError(ValueError):
    """A signup was attempted with a disposable / throwaway email domain.

    Carries honest, user-facing copy (the route surfaces it as a 422) — we tell the person plainly
    that a permanent address is required rather than silently dropping the signup.
    """

    def __init__(self, domain: str):
        self.domain = domain
        super().__init__(
            f"Please use a permanent email address. Disposable/throwaway email domains "
            f"(here: {domain!r}) aren't accepted for signup."
        )


class VelocityLimitError(Exception):
    """A per-IP acquisition action exceeded its window budget (the route maps this to 429)."""

    def __init__(self, action: str, limit: int, window_seconds: float):
        self.action = action
        self.limit = limit
        self.window_seconds = window_seconds
        super().__init__(
            f"too many {action} attempts from this address "
            f"({limit} per {int(window_seconds)}s) — please wait and try again"
        )


class SignupVelocityLimiter:
    """Fixed-window in-process per-IP counter for acquisition actions (signups + resends).

    Deliberately the SAME simple shape as api.public_routes._IpRateLimiter (mirrored, not shared:
    leads is its own concern). Keyed on (action, ip) so signups and verification-resends get
    INDEPENDENT budgets. ``allow`` is the boolean primitive; ``check`` raises VelocityLimitError
    for the route to translate to a 429. Bounded memory: idle keys are swept wholesale past a cap.

    Honest scope: in-process, so with N Fargate tasks the effective ceiling is N×limit. The
    CloudFront WAF rate rule is the real flood gate; this is the cheap, per-task, defense-in-depth
    layer that also protects the Anthropic-bill-adjacent signup path the WAF can't reason about.
    """

    _MAX_KEYS = 50_000  # bounded memory: drop idle keys wholesale once exceeded

    def __init__(self, limit: int, window_seconds: float, now: Callable[[], float] = time.time):
        self.limit = max(int(limit), 1)
        self.window_seconds = float(window_seconds)
        self.now = now
        self._hits: dict[tuple[str, str], list[float]] = {}

    def _key(self, action: str, ip: str) -> tuple[str, str]:
        return (action, ip or "0.0.0.0")

    def allow(self, action: str, ip: str) -> bool:
        cutoff = self.now() - self.window_seconds
        key = self._key(action, ip)
        hits = [t for t in self._hits.get(key, []) if t > cutoff]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(self.now())
        self._hits[key] = hits
        if len(self._hits) > self._MAX_KEYS:
            self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > cutoff}
        return True

    def check(self, action: str, ip: str) -> None:
        """Raise VelocityLimitError when the (action, ip) budget is exceeded; else record + pass."""
        if not self.allow(action, ip):
            raise VelocityLimitError(action, self.limit, self.window_seconds)


class CaptchaRequiredError(Exception):
    """A signup-start required a CAPTCHA token and the token was missing/invalid (route -> 400).

    Only ever raised once a real verifier is wired (the default seam never requires a token).
    """


class CaptchaVerifier:
    """A no-op-by-default seam where a CAPTCHA / Turnstile token COULD later be required.

    DEFAULT POSTURE = OPEN: ``required=False`` means ``verify`` always passes (returns None) and the
    signup route is byte-identical to having no captcha at all. This exists so a real provider
    (Cloudflare Turnstile / hCaptcha / reCAPTCHA) can be wired LATER in exactly one place without
    touching the route:

      * flip ``required=True`` (or set SIGNUP_CAPTCHA_REQUIRED=true → built in from_env), and
      * inject a ``token_validator(token, remote_ip) -> bool`` that calls the provider's
        siteverify endpoint (server-side, with the secret key from Secrets Manager).

    Until then there is NO network call and NO real provider — this is a seam, not an integration
    (see the PR body for the full wiring plan). When required but no validator is injected, the
    verifier FAILS CLOSED (rejects) so "required" can never silently mean "open".
    """

    def __init__(self, required: bool = False,
                 token_validator: Callable[[str, str | None], bool] | None = None):
        self.required = bool(required)
        self._validate = token_validator

    @classmethod
    def from_env(cls) -> "CaptchaVerifier":
        """Built default: OPEN unless SIGNUP_CAPTCHA_REQUIRED is exactly 'true'/'1'. No real
        validator is wired here (that lands with the provider integration) — so when required is
        flipped on without a validator, ``verify`` fails closed by design."""
        required = os.environ.get(ENV_CAPTCHA_REQUIRED, "") in ("true", "1")
        return cls(required=required)

    def verify(self, token: str | None, remote_ip: str | None = None) -> None:
        """Pass (return None) when not required. When required: reject a missing token, and reject
        when no validator is wired (fail closed) or the validator returns falsey."""
        if not self.required:
            return
        if not token:
            raise CaptchaRequiredError("a CAPTCHA token is required for signup")
        if self._validate is None:
            # 'required' was turned on but no provider validator was injected — refuse rather than
            # wave everyone through (that would make 'required' a no-op lie).
            raise CaptchaRequiredError("CAPTCHA is required but no verifier is configured")
        if not self._validate(token, remote_ip):
            raise CaptchaRequiredError("CAPTCHA verification failed")


ENV_CAPTCHA_REQUIRED = "SIGNUP_CAPTCHA_REQUIRED"  # exactly 'true'/'1' -> the seam demands a token


# Default acquisition-velocity budgets (per IP, per window). Conservative: generous enough for a
# real person fat-fingering, tight enough to make scripted abuse expensive. Overridable via env
# (shared/config.py mirrors these names).
DEFAULT_SIGNUP_LIMIT = 5          # signups per window per IP
DEFAULT_RESEND_LIMIT = 5          # verification-resends per window per IP
DEFAULT_VELOCITY_WINDOW_S = 3600  # 1 hour

# Action labels (the limiter keys on these — keep stable; they appear in 429 copy).
ACTION_SIGNUP = "signup"
ACTION_RESEND = "verification_resend"
