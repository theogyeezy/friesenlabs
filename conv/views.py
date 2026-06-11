"""Balto — NL view creation from chat (the synthesizing agent).

When a user asks the chat to *see* data (a view / graph / chart / visualization) that no existing
saved view covers, a managed synthesis path — Balto — builds a NEW tenant-scoped view as
declarative view-spec JSON over Cube, validated against `shared/schemas/view_spec.schema.json`.
Catalog components only, never code (CLAUDE.md hard constraint #7), and ONLY Uplift views are
manipulated — this module performs no raw-data writes of any kind (the one persistence path is the
existing saved-view store, and only on an explicit save).

The pieces:

- `detect_view_intent(message)` — the conv-layer trigger. `conv.session.Conversation.send`
  intercepts view-shaped utterances and returns a Turn flagged `view_intent=True` whose answer is
  the EXACT Balto status line (`BALTO_STATUS`) the chat must show while the agent works.
- `ViewSynthesizer` — the backend handler behind `POST /views/synthesize`:
    1. checks the ask against the tenant's EXISTING saved views (no duplicate synthesis when a
       saved view already covers it);
    2. validates the requested data against the semantic layer's member catalog
       (`cube.members(tenant_id=...)`) — when no Cube member can answer the ask, the honest
       `data_not_found` result carries `DATA_NOT_ON_PLATFORM`; a view is NEVER hallucinated;
    3. generates the spec through the existing `build_view` tool path (model generator +
       schema + real-member validation, reject-and-retry inside);
    4. returns the validated spec with a `draft_id`. Drafts are EPHEMERAL (in-memory, TTL'd,
       tenant-keyed): saving is a separate explicit step that persists through the existing
       `api.views.SavedViews` store; discarding is simply never saving.

THE TRUST RULE: `tenant_id` arrives from the verified Cognito JWT claim only (threaded by
`api.app`); drafts are keyed by that tenant and a draft can never be read or saved across tenants.
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any, Callable, Iterable

# The EXACT status line the chat shows while Balto works — owner-spec wording, do not edit.
BALTO_STATUS = "Our synthesizing agent Balto is mushing away to get this view for you."

# The honest answer when the semantic layer has no member that can answer the ask.
DATA_NOT_ON_PLATFORM = (
    "Your request cannot be fulfilled because the data does not exist on the platform."
)

# How long an unsaved draft stays addressable (save/discard is a human-paced choice in chat).
DEFAULT_DRAFT_TTL_S = 1800

# View-shaped utterances: the user asks to SEE data, not to act on it. Word-bounded so e.g.
# "review" never matches "view".
_VIEW_INTENT_RE = re.compile(
    r"\b(graphs?|charts?|plots?|dashboards?|visuali[sz]ations?|visuali[sz]e[ds]?|views?)\b",
    re.IGNORECASE,
)

# Words that carry no data meaning when matching an ask against the member catalog or an
# existing saved view (articles/fillers + the intent words themselves + chart-shape words).
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "to", "in", "on", "by", "per", "and", "or", "vs",
    "me", "my", "our", "your", "i", "we", "you", "us", "it", "its", "this", "that", "these",
    "show", "see", "get", "give", "make", "build", "create", "display", "draw", "want", "need",
    "would", "like", "can", "could", "please", "with", "as", "at", "over", "across", "all",
    "graph", "graphs", "chart", "charts", "plot", "plots", "dashboard", "dashboards",
    "visualization", "visualizations", "visualisation", "visualisations",
    "visualize", "visualized", "visualizes", "visualise", "visualised", "visualises",
    "view", "views", "bar", "line", "pie", "kpi", "table", "number", "numbers",
    "breakdown", "broken", "down", "up", "what", "how", "many", "much", "is", "are",
    "last", "this", "month", "week", "quarter", "year", "daily", "weekly", "monthly", "time",
    "new", "total", "count",
})

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def detect_view_intent(message: Any) -> bool:
    """True when the utterance asks to SEE data — a view / graph / chart / visualization."""
    return bool(isinstance(message, str) and _VIEW_INTENT_RE.search(message))


def _singular(token: str) -> str:
    """Naive singularization so 'deals' matches 'Deals.count' and vice versa."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def request_tokens(request: str) -> set[str]:
    """The CONTENT tokens of an ask — lowercased, stopword/intent-word-stripped, singularized."""
    out: set[str] = set()
    for word in _WORD_RE.findall(request or ""):
        low = word.lower()
        if low in _STOPWORDS:
            continue
        out.add(_singular(low))
    return out


def member_tokens(members: Iterable[str]) -> set[str]:
    """Tokens of the Cube member catalog: 'Deals.totalValue' -> {'deal', 'total', 'value'}."""
    out: set[str] = set()
    for member in members or []:
        if not isinstance(member, str):
            continue
        for piece in re.split(r"[._]", member):
            for word in _CAMEL_RE.split(piece):
                low = word.lower()
                if low:
                    out.add(_singular(low))
    return out


def members_cover(request: str, members: Iterable[str]) -> bool:
    """True when the member catalog plausibly holds the requested data.

    Requires at least one content token of the ask to appear in the catalog's tokens — the
    fail-closed gate behind the honest data-not-found answer (never hallucinate a view). An ask
    with NO content tokens ("show me a chart") passes: there is nothing to disprove, and the
    generator is still bound to the real catalog downstream.
    """
    wanted = request_tokens(request)
    if not wanted:
        return True
    return bool(wanted & member_tokens(members))


def find_covering_view(request: str, rows: Iterable[dict]) -> dict | None:
    """The tenant's existing saved view that already covers the ask, if any.

    Coverage = every content token of the ask appears in the view's title + source prompt +
    view id token set (an empty ask covers nothing). Deliberately strict: a partial overlap
    synthesizes a NEW view rather than answering with a near-miss.
    """
    wanted = request_tokens(request)
    if not wanted:
        return None
    for row in rows or []:
        spec = row.get("spec_json") or {}
        haystack = " ".join(
            str(part)
            for part in (spec.get("title"), row.get("source_prompt"), row.get("view_id"))
            if part
        )
        have = {_singular(w.lower()) for w in _WORD_RE.findall(haystack)}
        if wanted <= have:
            return row
    return None


class ViewSynthesizer:
    """Balto's backend: intent-vs-saved-views check, catalog check, build_view generation, drafts.

    Injected pieces (all offline-safe to construct):
      - `saved_views`: the existing `api.views.SavedViews` facade (its store lists/persists);
      - `cube`: the tenant-scoped member catalog (`agents.tools.cube_client.CubeClient` protocol);
      - `generator`: the view-spec generator `build_view` accepts (e.g. AnthropicSpecGenerator).

    Results are plain dicts keyed by `status`:
      exists | data_not_found | unavailable | invalid | ok
    `ok` carries `draft_id` + the validated `spec`; nothing is persisted until `save_draft`.
    """

    def __init__(
        self,
        *,
        saved_views: Any,
        cube: Any = None,
        generator: Any = None,
        agent: str = "balto",
        draft_ttl_s: int = DEFAULT_DRAFT_TTL_S,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._saved_views = saved_views
        self._cube = cube
        self._generator = generator
        self._agent = agent
        self._ttl = draft_ttl_s
        self._now = now
        self._lock = threading.Lock()
        # (tenant_id, draft_id) -> {"spec", "request", "created"} — tenant-keyed, TTL-evicted.
        self._drafts: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------------ synthesis
    def synthesize(self, tenant_id: str, request: str) -> dict:
        if not isinstance(request, str) or not request.strip():
            return {"status": "invalid", "error": "empty view request"}

        # (1) An existing saved view that already covers the ask — no duplicate synthesis.
        rows = self._saved_views.store.list(tenant_id)
        covering = find_covering_view(request, rows)
        if covering is not None:
            return {"status": "exists", "view": covering}

        # (2) The semantic layer's member catalog gates everything downstream (never hallucinate).
        if self._cube is None or not getattr(self._cube, "configured", True):
            return {
                "status": "unavailable",
                "error": "the semantic layer is not configured on this deployment",
            }
        members = list(self._cube.members(tenant_id=tenant_id))
        if not members or not members_cover(request, members):
            return {"status": "data_not_found", "message": DATA_NOT_ON_PLATFORM}

        # (3) Generate through the existing build_view tool path (schema + real-member
        # validation, reject-and-retry, defense-in-depth re-validation — all inside the tool).
        if self._generator is None:
            return {"status": "unavailable", "error": "view generator not configured"}
        from agents.tools.base import ToolContext  # noqa: PLC0415 — keep module import light
        from agents.tools.build_view import BuildView  # noqa: PLC0415

        tool = BuildView(generator=self._generator, cube_client=self._cube)
        ctx = ToolContext(tenant_id=tenant_id, agent=self._agent, cube=self._cube, extra={})
        out = tool.invoke(ctx, request=request)
        result = out.get("result") or {}
        if result.get("status") != "valid" or not isinstance(result.get("spec"), dict):
            return {
                "status": "invalid",
                "error": result.get("error") or "spec generation failed",
                "attempts": result.get("attempts"),
            }

        # (4) Park the validated spec as an ephemeral, tenant-keyed draft.
        spec = result["spec"]
        draft_id = uuid.uuid4().hex
        with self._lock:
            self._evict_locked()
            self._drafts[(str(tenant_id), draft_id)] = {
                "spec": spec,
                "request": request,
                "created": self._now(),
            }
        return {
            "status": "ok",
            "draft_id": draft_id,
            "spec": spec,
            "attempts": result.get("attempts"),
        }

    # ------------------------------------------------------------------ drafts
    def get_draft(self, tenant_id: str, draft_id: str) -> dict | None:
        """This tenant's draft, or None — a draft id never resolves across tenants."""
        with self._lock:
            self._evict_locked()
            draft = self._drafts.get((str(tenant_id), str(draft_id)))
            return dict(draft) if draft else None

    def save_draft(self, tenant_id: str, draft_id: str, *, created_by: str = "") -> dict | None:
        """Persist a draft through the EXISTING saved-view store (versioned, re-validated there).

        Returns the saved row, or None when the draft does not exist for THIS tenant. The draft
        is removed only after a successful save — a validation failure leaves it intact.
        """
        draft = self.get_draft(tenant_id, draft_id)
        if draft is None:
            return None
        row = self._saved_views.save(
            tenant_id,
            draft["spec"],
            source_prompt=draft.get("request", ""),
            created_by=created_by,
        )
        self.discard_draft(tenant_id, draft_id)
        return row

    def discard_draft(self, tenant_id: str, draft_id: str) -> None:
        """Drop a draft (the explicit discard path — also called after a successful save)."""
        with self._lock:
            self._drafts.pop((str(tenant_id), str(draft_id)), None)

    def _evict_locked(self) -> None:
        cutoff = self._now() - self._ttl
        stale = [k for k, v in self._drafts.items() if v.get("created", 0) < cutoff]
        for key in stale:
            del self._drafts[key]
