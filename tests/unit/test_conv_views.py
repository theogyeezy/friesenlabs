"""Unit: Balto view synthesis (conv/views.py) — intent, catalog gate, drafts, save round-trip."""
import pytest

from api.views import SavedViews
from conv.views import (
    BALTO_STATUS,
    DATA_NOT_ON_PLATFORM,
    ViewSynthesizer,
    detect_view_intent,
    find_covering_view,
    members_cover,
)

MEMBERS = ["Deals.count", "Deals.totalValue", "Deals.stage", "Contacts.count"]

VALID_SPEC = {
    "view_id": "deals_by_stage",
    "title": "Deals by stage",
    "semantic_refs": ["Deals.count", "Deals.stage"],
    "layout": [
        {
            "type": "chart",
            "encoding": "vega-lite",
            "spec": {"mark": "bar"},
            "query": {"measures": ["Deals.count"], "dimensions": ["Deals.stage"]},
        }
    ],
}


class FakeCube:
    """Member catalog stub honoring the CubeClient protocol bits Balto uses."""

    configured = True

    def __init__(self, members=MEMBERS):
        self._members = list(members)
        self.calls = []

    def members(self, *, tenant_id):
        self.calls.append(tenant_id)
        return list(self._members)


class FakeGenerator:
    """Spec-generator stub (the .generate contract build_view accepts)."""

    def __init__(self, spec=VALID_SPEC, valid=True, errors=None):
        self._spec = spec
        self._valid = valid
        self._errors = errors or []

    def generate(self, *, request, allowed_members):
        return {
            "valid": self._valid,
            "spec": dict(self._spec) if self._spec else None,
            "errors": list(self._errors),
            "attempts": 1,
        }


def _synth(*, cube=None, generator=None, saved=None):
    return ViewSynthesizer(
        saved_views=saved or SavedViews(allowed_members=set(MEMBERS)),
        cube=cube,
        generator=generator,
    )


# ------------------------------------------------------------------ owner-spec strings
def test_balto_status_line_is_exact():
    # Owner spec: the chat MUST show exactly this while the agent works.
    assert BALTO_STATUS == (
        "Our synthesizing agent Balto is mushing away to get this view for you."
    )


def test_data_not_found_message_names_the_platform():
    assert DATA_NOT_ON_PLATFORM == (
        "Your request cannot be fulfilled because the data does not exist on the platform."
    )


# ------------------------------------------------------------------ intent detection
@pytest.mark.parametrize("message", [
    "Show me a graph of deals by stage",
    "can I get a chart of revenue?",
    "build a dashboard for contacts",
    "visualize deals by owner",
    "visualise pipeline value please",
    "I want a view of won deals",
    "plot contacts by region",
])
def test_view_intent_positive(message):
    assert detect_view_intent(message)


@pytest.mark.parametrize("message", [
    "send an email to the Acme lead",
    "how is my pipeline?",
    "review our pricing with the team",  # 'review' must not match 'view'
    "update the deal to negotiation",
    "",
    None,
])
def test_view_intent_negative(message):
    assert not detect_view_intent(message)


# ------------------------------------------------------------------ catalog coverage
def test_members_cover_matches_request_nouns():
    assert members_cover("a chart of deals by stage", MEMBERS)
    assert members_cover("graph contact growth", MEMBERS)  # singular/plural both ways


def test_members_cover_rejects_data_not_on_platform():
    assert not members_cover("graph the daily weather in Austin", MEMBERS)


def test_members_cover_with_no_content_tokens_passes():
    # Nothing to disprove; the generator stays bound to the real catalog downstream.
    assert members_cover("show me a chart", MEMBERS)


# ------------------------------------------------------------------ existing-view check
def test_find_covering_view_matches_title_and_prompt():
    rows = [{
        "view_id": "pipeline_overview",
        "spec_json": {"title": "Pipeline overview"},
        "source_prompt": "Show me total pipeline and value by stage",
    }]
    assert find_covering_view("view pipeline value by stage", rows) is rows[0]
    # A partial overlap is NOT coverage — synthesize a new view instead.
    assert find_covering_view("chart of deals by owner", rows) is None


# ------------------------------------------------------------------ synthesizer paths
def test_synthesize_ok_returns_validated_spec_with_draft_id():
    synth = _synth(cube=FakeCube(), generator=FakeGenerator())
    out = synth.synthesize("tenant-A", "show me a chart of deals by stage")
    assert out["status"] == "ok"
    assert out["spec"]["view_id"] == "deals_by_stage"
    assert out["draft_id"]
    # The draft is retrievable by THIS tenant only.
    assert synth.get_draft("tenant-A", out["draft_id"])["spec"] == out["spec"]
    assert synth.get_draft("tenant-B", out["draft_id"]) is None


def test_synthesize_data_not_found_when_no_member_can_answer():
    synth = _synth(cube=FakeCube(), generator=FakeGenerator())
    out = synth.synthesize("tenant-A", "graph the daily weather in Austin")
    assert out["status"] == "data_not_found"
    assert out["message"] == DATA_NOT_ON_PLATFORM


def test_synthesize_data_not_found_on_empty_catalog():
    synth = _synth(cube=FakeCube(members=[]), generator=FakeGenerator())
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    assert out["status"] == "data_not_found"


def test_synthesize_unavailable_without_semantic_layer():
    synth = _synth(cube=None, generator=FakeGenerator())
    assert synth.synthesize("tenant-A", "chart of deals")["status"] == "unavailable"


def test_synthesize_unavailable_without_generator():
    synth = _synth(cube=FakeCube(), generator=None)
    assert synth.synthesize("tenant-A", "chart of deals")["status"] == "unavailable"


def test_synthesize_exists_when_a_saved_view_already_covers_it():
    saved = SavedViews(allowed_members=set(MEMBERS))
    saved.save("tenant-A", dict(VALID_SPEC), source_prompt="deals by stage", created_by="u1")
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(), saved=saved)
    out = synth.synthesize("tenant-A", "show me deals by stage")
    assert out["status"] == "exists"
    assert out["view"]["view_id"] == "deals_by_stage"


def test_existing_view_check_is_tenant_scoped():
    saved = SavedViews(allowed_members=set(MEMBERS))
    saved.save("tenant-B", dict(VALID_SPEC), source_prompt="deals by stage", created_by="u1")
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(), saved=saved)
    # Tenant A doesn't see B's view — a fresh synthesis runs for A.
    assert synth.synthesize("tenant-A", "show me deals by stage")["status"] == "ok"


def test_schema_invalid_spec_is_rejected_never_returned():
    bad = {"view_id": "x", "title": "x", "semantic_refs": [], "layout": []}  # violates schema
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(spec=bad))
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    assert out["status"] == "invalid"
    assert "spec" not in out


def test_unknown_member_spec_is_rejected():
    rogue = dict(VALID_SPEC, semantic_refs=["Secrets.count"])
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(spec=rogue))
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    assert out["status"] == "invalid"


def test_generator_failure_surfaces_invalid_with_error():
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(spec=None, valid=False,
                                                            errors=["model call failed"]))
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    assert out["status"] == "invalid"
    assert "model call failed" in out["error"]


def test_empty_request_is_invalid():
    synth = _synth(cube=FakeCube(), generator=FakeGenerator())
    assert synth.synthesize("tenant-A", "   ")["status"] == "invalid"


# ------------------------------------------------------------------ draft save round-trip
def test_save_draft_persists_via_existing_store_and_pops_the_draft():
    saved = SavedViews(allowed_members=set(MEMBERS))
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(), saved=saved)
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    row = synth.save_draft("tenant-A", out["draft_id"], created_by="u1")
    assert row["version"] == 1
    assert saved.get("tenant-A", "deals_by_stage")["spec_json"]["title"] == "Deals by stage"
    # Saved through the EXISTING store, draft consumed (discard-after-save).
    assert synth.get_draft("tenant-A", out["draft_id"]) is None


def test_save_draft_is_tenant_scoped():
    synth = _synth(cube=FakeCube(), generator=FakeGenerator())
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    assert synth.save_draft("tenant-B", out["draft_id"], created_by="mallory") is None
    # A's draft is still there (the cross-tenant attempt consumed nothing).
    assert synth.get_draft("tenant-A", out["draft_id"]) is not None


def test_discard_draft_is_ephemeral_and_persists_nothing():
    saved = SavedViews(allowed_members=set(MEMBERS))
    synth = _synth(cube=FakeCube(), generator=FakeGenerator(), saved=saved)
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    synth.discard_draft("tenant-A", out["draft_id"])
    assert synth.get_draft("tenant-A", out["draft_id"]) is None
    assert saved.store.list("tenant-A") == []


def test_drafts_expire_after_ttl():
    clock = {"t": 1000.0}
    synth = ViewSynthesizer(
        saved_views=SavedViews(allowed_members=set(MEMBERS)),
        cube=FakeCube(),
        generator=FakeGenerator(),
        draft_ttl_s=60,
        now=lambda: clock["t"],
    )
    out = synth.synthesize("tenant-A", "chart of deals by stage")
    clock["t"] += 61
    assert synth.get_draft("tenant-A", out["draft_id"]) is None
