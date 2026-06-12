"""Connector hardening tests — no live calls, fixture/recorded responses only.

Covers the pagination, datetime-filter, param-naming, and empty-page-termination
contracts for the three REST clients (HubSpotRestClient, GoHighLevelRestClient,
StripeRestClient).  Every interaction with the network layer is monkey-patched
via `_post`/`_get` overrides so no real network is touched.

What each section verifies:
  HubSpot  — CRM v3 Search API (POST /crm/v3/objects/{type}/search)
    * pagination follows paging.next.after to exhaustion (multi-page)
    * ISO-8601 `since` string is sent verbatim as the filter `value`
    * filter uses operator "GT" and the correct `propertyName` per object type
    * contacts use `lastmodifieddate`, others use `hs_lastmodifieddate`
    * an empty `paging` (or missing `next`) terminates the loop
    * None `since` emits no filterGroups key at all

  GoHighLevel — API v2
    * contacts: pagination via `meta.startAfterId` cursor param
    * opportunities: pagination via `meta.nextPage` → `page` param
    * params named `locationId` (contacts) and `location_id` (opportunities)
    * Version header is "2021-07-28"
    * client-side `since` filter is applied correctly (dateUpdated/updatedAt)
    * empty page / null cursor terminates both loops

  Stripe — List API
    * `created[gt]` receives the integer epoch value from the zero-padded cursor
    * `starting_after` is set to the last item's `id` on multi-page responses
    * `status=all` is sent for subscriptions, absent for customers/invoices
    * `has_more=False` terminates the loop; missing/empty `data` also terminates
"""
from __future__ import annotations

import pytest

from ingest.connectors.hubspot import HubSpotRestClient, _LASTMOD_PROP, _SEARCH_PROPERTIES
from ingest.connectors.gohighlevel import GoHighLevelRestClient, GHL_API_VERSION
from ingest.connectors.stripe_data import StripeRestClient


# ============================================================================ #
# Helpers
# ============================================================================ #

class _HS:
    """Namespace for HubSpot test helpers."""

    @staticmethod
    def make_client(pages: list[dict], *, token: str = "hs-tok") -> HubSpotRestClient:
        """Return a HubSpotRestClient whose _post yields from `pages` in order."""
        c = HubSpotRestClient(base_url="https://hub.example.test")
        c.set_token(token)
        call_log: list[tuple[str, dict]] = []
        _pages = iter(pages)

        def fake_post(path: str, payload: dict) -> dict:
            call_log.append((path, payload))
            try:
                return next(_pages)
            except StopIteration:
                return {"results": []}

        c._post = fake_post  # type: ignore[method-assign]
        c._call_log = call_log  # type: ignore[attr-defined]
        return c

    @staticmethod
    def result_page(results: list[dict], after: str | None = None) -> dict:
        page: dict = {"results": results}
        if after:
            page["paging"] = {"next": {"after": after}}
        return page


class _GHL:
    """Namespace for GoHighLevel test helpers."""

    @staticmethod
    def make_client(
        contacts_pages: list[dict] | None = None,
        opps_pages: list[dict] | None = None,
        *,
        token: str = "ghl-tok",
        location_id: str = "loc-999",
    ) -> GoHighLevelRestClient:
        c = GoHighLevelRestClient(base_url="https://ghl.example.test")
        c.set_token(token)
        c.set_location(location_id)

        _contact_pages = iter(contacts_pages or [])
        _opp_pages = iter(opps_pages or [])
        call_log: list[tuple[str, dict]] = []

        def fake_get(path: str, params: dict) -> dict:
            call_log.append((path, params))
            if "contacts" in path:
                try:
                    return next(_contact_pages)
                except StopIteration:
                    return {"contacts": [], "meta": {}}
            else:
                try:
                    return next(_opp_pages)
                except StopIteration:
                    return {"opportunities": [], "meta": {}}

        c._get = fake_get  # type: ignore[method-assign]
        c._call_log = call_log  # type: ignore[attr-defined]
        return c

    @staticmethod
    def contacts_page(items: list[dict], start_after_id: str | None = None) -> dict:
        return {"contacts": items, "meta": {"startAfterId": start_after_id}}

    @staticmethod
    def opps_page(items: list[dict], next_page: int | None = None) -> dict:
        return {"opportunities": items, "meta": {"nextPage": next_page}}

    @staticmethod
    def contact(id_: str, date_updated: str = "2026-06-01T00:00:00.000Z") -> dict:
        return {"id": id_, "contactName": f"Contact {id_}", "dateUpdated": date_updated}

    @staticmethod
    def opportunity(id_: str, updated_at: str = "2026-06-01T00:00:00.000Z") -> dict:
        return {"id": id_, "name": f"Opp {id_}", "updatedAt": updated_at, "status": "open"}


class _ST:
    """Namespace for Stripe test helpers."""

    @staticmethod
    def make_client(pages: list[dict], *, key: str = "rk_test") -> StripeRestClient:
        c = StripeRestClient(base_url="https://stripe.example.test")
        c.set_key(key)
        # Capture a snapshot copy of params at call time (params dict is mutated
        # in-place by _list across pages, so we must copy to record the state
        # at each call boundary).
        call_log: list[tuple[str, dict]] = []
        _pages = iter(pages)

        def fake_get(path: str, params: dict) -> dict:
            call_log.append((path, dict(params)))  # snapshot copy
            try:
                return next(_pages)
            except StopIteration:
                return {"data": [], "has_more": False}

        c._get = fake_get  # type: ignore[method-assign]
        c._call_log = call_log  # type: ignore[attr-defined]
        return c

    @staticmethod
    def page(items: list[dict], has_more: bool = False) -> dict:
        return {"data": items, "has_more": has_more}

    @staticmethod
    def customer(id_: str, created: int = 1_748_000_000) -> dict:
        return {"id": id_, "object": "customer", "name": f"Cust {id_}", "created": created}

    @staticmethod
    def subscription(id_: str, created: int = 1_748_000_000) -> dict:
        return {
            "id": id_, "object": "subscription", "status": "active",
            "customer": "cus_XXX", "created": created,
            "items": {"data": [{"price": {"nickname": "Starter", "unit_amount": 9900,
                                          "currency": "usd", "recurring": {"interval": "month"}}}]},
        }

    @staticmethod
    def invoice(id_: str, created: int = 1_748_000_000) -> dict:
        return {
            "id": id_, "object": "invoice", "number": id_, "customer": "cus_XXX",
            "status": "paid", "amount_paid": 9900, "currency": "usd", "created": created,
        }


# ============================================================================ #
# HubSpot tests
# ============================================================================ #

@pytest.mark.unit
class TestHubSpotLastmodProps:
    """Property names match the HubSpot CRM v3 documented field names."""

    def test_contacts_use_lastmodifieddate(self):
        assert _LASTMOD_PROP["contacts"] == "lastmodifieddate"

    def test_companies_use_hs_lastmodifieddate(self):
        assert _LASTMOD_PROP["companies"] == "hs_lastmodifieddate"

    def test_deals_use_hs_lastmodifieddate(self):
        assert _LASTMOD_PROP["deals"] == "hs_lastmodifieddate"

    def test_notes_use_hs_lastmodifieddate(self):
        assert _LASTMOD_PROP["notes"] == "hs_lastmodifieddate"

    def test_lastmod_prop_in_search_properties_for_each_type(self):
        """Every type lists its lastmod property in the search `properties` array."""
        for object_type, prop in _LASTMOD_PROP.items():
            assert prop in _SEARCH_PROPERTIES[object_type], (
                f"{object_type}: lastmod prop {prop!r} missing from _SEARCH_PROPERTIES"
            )


@pytest.mark.unit
class TestHubSpotPagination:
    """_search follows paging.next.after across multiple pages to exhaustion."""

    def test_single_page_no_paging_returns_all_results(self):
        results = [{"id": "1"}, {"id": "2"}]
        c = _HS.make_client([_HS.result_page(results)])
        items = list(c._search("companies", None))
        assert items == results

    def test_multi_page_follows_after_cursor(self):
        page1 = _HS.result_page([{"id": "A"}], after="cursor-1")
        page2 = _HS.result_page([{"id": "B"}], after="cursor-2")
        page3 = _HS.result_page([{"id": "C"}])  # no paging
        c = _HS.make_client([page1, page2, page3])
        items = list(c._search("deals", None))
        assert [i["id"] for i in items] == ["A", "B", "C"]

    def test_after_cursor_sent_in_request_body(self):
        page1 = _HS.result_page([{"id": "X"}], after="tok-abc")
        page2 = _HS.result_page([])
        c = _HS.make_client([page1, page2])
        list(c._search("contacts", None))
        # second call must include the `after` key
        assert c._call_log[1][1].get("after") == "tok-abc"

    def test_empty_paging_terminates(self):
        page1 = {"results": [{"id": "Z"}]}  # no paging key at all
        c = _HS.make_client([page1])
        items = list(c._search("notes", None))
        assert len(items) == 1
        assert len(c._call_log) == 1  # only one request

    def test_empty_results_page_with_no_paging_terminates_on_first_call(self):
        # A page with no results AND no paging.next terminates immediately.
        page1 = _HS.result_page([])  # no paging key
        c = _HS.make_client([page1])
        items = list(c._search("companies", None))
        assert items == []
        assert len(c._call_log) == 1

    def test_empty_results_page_with_cursor_makes_one_more_call(self):
        # A page with zero results but an `after` cursor will follow the cursor
        # once more; the subsequent empty-no-paging response terminates.
        page1 = _HS.result_page([], after="next-cursor")
        page2 = _HS.result_page([])  # no paging — terminates
        c = _HS.make_client([page1, page2])
        items = list(c._search("companies", None))
        assert items == []
        assert len(c._call_log) == 2
        assert c._call_log[1][1].get("after") == "next-cursor"


@pytest.mark.unit
class TestHubSpotSinceFilter:
    """ISO-8601 `since` strings are sent verbatim as the filter `value`."""

    def test_since_sets_filter_groups_with_gt_operator(self):
        since_ts = "2026-05-01T00:00:00.000Z"
        c = _HS.make_client([_HS.result_page([])])
        list(c._search("contacts", since_ts))
        payload = c._call_log[0][1]
        fgroups = payload.get("filterGroups", [])
        assert len(fgroups) == 1
        flt = fgroups[0]["filters"][0]
        assert flt["operator"] == "GT"
        assert flt["value"] == since_ts

    def test_since_uses_correct_property_name_per_object_type(self):
        since_ts = "2026-06-01T00:00:00.000Z"
        for obj_type, expected_prop in _LASTMOD_PROP.items():
            c = _HS.make_client([_HS.result_page([])])
            list(c._search(obj_type, since_ts))
            flt = c._call_log[0][1]["filterGroups"][0]["filters"][0]
            assert flt["propertyName"] == expected_prop, (
                f"{obj_type}: expected propertyName={expected_prop!r}, got {flt['propertyName']!r}"
            )

    def test_none_since_emits_no_filter_groups(self):
        c = _HS.make_client([_HS.result_page([])])
        list(c._search("deals", None))
        payload = c._call_log[0][1]
        assert "filterGroups" not in payload

    def test_since_value_is_not_converted_to_epoch_millis(self):
        """The filter value is the raw ISO-8601 string, not a numeric epoch."""
        since_ts = "2026-05-15T12:30:00.000Z"
        c = _HS.make_client([_HS.result_page([])])
        list(c._search("companies", since_ts))
        flt = c._call_log[0][1]["filterGroups"][0]["filters"][0]
        # Must be a string, not a number
        assert isinstance(flt["value"], str)
        assert flt["value"] == since_ts


@pytest.mark.unit
class TestHubSpotSortAndProperties:
    """Each request sorts by the lastmod property ascending."""

    def test_sort_by_lastmod_ascending(self):
        for obj_type, lastmod in _LASTMOD_PROP.items():
            c = _HS.make_client([_HS.result_page([])])
            list(c._search(obj_type, None))
            sorts = c._call_log[0][1].get("sorts", [])
            assert len(sorts) == 1
            assert sorts[0]["propertyName"] == lastmod
            assert sorts[0]["direction"] == "ASCENDING"

    def test_properties_array_is_sent(self):
        c = _HS.make_client([_HS.result_page([])])
        list(c._search("contacts", None))
        props = c._call_log[0][1].get("properties", [])
        assert "email" in props
        assert "lastmodifieddate" in props


# ============================================================================ #
# GoHighLevel tests
# ============================================================================ #

@pytest.mark.unit
class TestGHLVersionHeader:
    """The canonical GHL v2 Version header value is "2021-07-28"."""

    def test_version_constant_value(self):
        assert GHL_API_VERSION == "2021-07-28"


@pytest.mark.unit
class TestGHLContactsPagination:
    """list_contacts pages via meta.startAfterId using the `startAfterId` param."""

    def test_single_page_null_cursor_terminates(self):
        page = _GHL.contacts_page([_GHL.contact("c1"), _GHL.contact("c2")])
        c = _GHL.make_client(contacts_pages=[page])
        items = list(c.list_contacts(None))
        assert [i["id"] for i in items] == ["c1", "c2"]
        assert len(c._call_log) == 1

    def test_multi_page_follows_start_after_id_cursor(self):
        p1 = _GHL.contacts_page([_GHL.contact("c1")], start_after_id="c1")
        p2 = _GHL.contacts_page([_GHL.contact("c2")])  # null cursor
        c = _GHL.make_client(contacts_pages=[p1, p2])
        items = list(c.list_contacts(None))
        assert [i["id"] for i in items] == ["c1", "c2"]

    def test_start_after_id_param_name_in_request(self):
        """Param must be `startAfterId` (GHL v2 documented cursor param name)."""
        p1 = _GHL.contacts_page([_GHL.contact("c1")], start_after_id="c1")
        p2 = _GHL.contacts_page([_GHL.contact("c2")])
        c = _GHL.make_client(contacts_pages=[p1, p2])
        list(c.list_contacts(None))
        # First call: startAfterId must be None (omitted via the dict filter)
        first_params = c._call_log[0][1]
        assert "locationId" in first_params
        # Second call: startAfterId must equal the cursor from page 1
        second_params = c._call_log[1][1]
        assert second_params.get("startAfterId") == "c1"

    def test_location_id_param_name_in_request(self):
        """Contacts endpoint uses `locationId` (camelCase) as the location param."""
        c = _GHL.make_client(contacts_pages=[_GHL.contacts_page([])])
        list(c.list_contacts(None))
        params = c._call_log[0][1]
        assert "locationId" in params
        assert params["locationId"] == "loc-999"

    def test_empty_contacts_terminates(self):
        c = _GHL.make_client(contacts_pages=[_GHL.contacts_page([])])
        items = list(c.list_contacts(None))
        assert items == []
        assert len(c._call_log) == 1

    def test_since_filter_applied_client_side_on_date_updated(self):
        """Since filtering is client-side on dateUpdated (server-side not confirmed)."""
        old = _GHL.contact("c-old", "2026-05-01T00:00:00.000Z")
        new = _GHL.contact("c-new", "2026-06-01T00:00:00.000Z")
        page = _GHL.contacts_page([old, new])
        c = _GHL.make_client(contacts_pages=[page])
        # Client-side: only contacts newer than the since cursor are yielded
        items = list(c.list_contacts("2026-05-15T00:00:00.000Z"))
        assert len(items) == 1
        assert items[0]["id"] == "c-new"

    def test_none_since_yields_all_contacts(self):
        old = _GHL.contact("c-old", "2026-05-01T00:00:00.000Z")
        new = _GHL.contact("c-new", "2026-06-01T00:00:00.000Z")
        page = _GHL.contacts_page([old, new])
        c = _GHL.make_client(contacts_pages=[page])
        items = list(c.list_contacts(None))
        assert len(items) == 2


@pytest.mark.unit
class TestGHLOpportunitiesPagination:
    """list_opportunities pages via meta.nextPage using the `page` param."""

    def test_single_page_null_next_terminates(self):
        page = _GHL.opps_page([_GHL.opportunity("o1"), _GHL.opportunity("o2")])
        c = _GHL.make_client(opps_pages=[page])
        items = list(c.list_opportunities(None))
        assert [i["id"] for i in items] == ["o1", "o2"]
        assert len(c._call_log) == 1

    def test_multi_page_follows_next_page_number(self):
        p1 = _GHL.opps_page([_GHL.opportunity("o1")], next_page=2)
        p2 = _GHL.opps_page([_GHL.opportunity("o2")])  # no next page
        c = _GHL.make_client(opps_pages=[p1, p2])
        items = list(c.list_opportunities(None))
        assert [i["id"] for i in items] == ["o1", "o2"]

    def test_page_param_name_in_request(self):
        """Param must be `page` (GHL v2 documented opportunities search param)."""
        p1 = _GHL.opps_page([_GHL.opportunity("o1")], next_page=2)
        p2 = _GHL.opps_page([_GHL.opportunity("o2")])
        c = _GHL.make_client(opps_pages=[p1, p2])
        list(c.list_opportunities(None))
        # First request: page=1
        assert c._call_log[0][1].get("page") == 1
        # Second request: page=2 (from meta.nextPage)
        assert c._call_log[1][1].get("page") == 2

    def test_location_id_param_name_in_request(self):
        """Opportunities endpoint uses `location_id` (snake_case)."""
        c = _GHL.make_client(opps_pages=[_GHL.opps_page([])])
        list(c.list_opportunities(None))
        params = c._call_log[0][1]
        assert "location_id" in params
        assert params["location_id"] == "loc-999"

    def test_empty_opportunities_terminates(self):
        c = _GHL.make_client(opps_pages=[_GHL.opps_page([])])
        items = list(c.list_opportunities(None))
        assert items == []
        assert len(c._call_log) == 1

    def test_since_filter_applied_client_side_on_updated_at(self):
        old = _GHL.opportunity("o-old", "2026-05-01T00:00:00.000Z")
        new = _GHL.opportunity("o-new", "2026-06-01T00:00:00.000Z")
        page = _GHL.opps_page([old, new])
        c = _GHL.make_client(opps_pages=[page])
        items = list(c.list_opportunities("2026-05-15T00:00:00.000Z"))
        assert len(items) == 1
        assert items[0]["id"] == "o-new"


# ============================================================================ #
# Stripe tests
# ============================================================================ #

@pytest.mark.unit
class TestStripeCreatedGtFilter:
    """`created[gt]` receives the integer epoch parsed from the zero-padded cursor."""

    def test_since_as_zero_padded_epoch_string_becomes_int(self):
        c = _ST.make_client([_ST.page([])])
        list(c._list("/v1/customers", "1748000000"))
        params = c._call_log[0][1]
        assert "created[gt]" in params
        assert params["created[gt]"] == 1_748_000_000
        assert isinstance(params["created[gt]"], int)

    def test_none_since_omits_created_gt(self):
        c = _ST.make_client([_ST.page([])])
        list(c._list("/v1/customers", None))
        params = c._call_log[0][1]
        assert "created[gt]" not in params

    def test_malformed_cursor_skips_filter_safe_full_pull(self):
        """A non-integer cursor must not crash — falls back to a full pull."""
        c = _ST.make_client([_ST.page([])])
        list(c._list("/v1/customers", "not-an-epoch"))
        params = c._call_log[0][1]
        assert "created[gt]" not in params


@pytest.mark.unit
class TestStripeStartingAfterPagination:
    """`starting_after` is set to the last item's `id` on multi-page responses."""

    def test_single_page_has_more_false_terminates(self):
        items = [_ST.customer("cus_1"), _ST.customer("cus_2")]
        c = _ST.make_client([_ST.page(items, has_more=False)])
        result = list(c._list("/v1/customers", None))
        assert [i["id"] for i in result] == ["cus_1", "cus_2"]
        assert len(c._call_log) == 1

    def test_multi_page_follows_starting_after_last_id(self):
        p1 = _ST.page([_ST.customer("cus_A"), _ST.customer("cus_B")], has_more=True)
        p2 = _ST.page([_ST.customer("cus_C")], has_more=False)
        c = _ST.make_client([p1, p2])
        result = list(c._list("/v1/customers", None))
        assert [i["id"] for i in result] == ["cus_A", "cus_B", "cus_C"]
        # second request must include starting_after=cus_B (last id from page 1)
        assert c._call_log[1][1].get("starting_after") == "cus_B"

    def test_first_page_has_no_starting_after(self):
        p1 = _ST.page([_ST.customer("cus_A")], has_more=True)
        p2 = _ST.page([], has_more=False)
        c = _ST.make_client([p1, p2])
        list(c._list("/v1/customers", None))
        assert "starting_after" not in c._call_log[0][1]

    def test_has_more_false_with_items_terminates_immediately(self):
        items = [_ST.customer("cus_X")]
        c = _ST.make_client([_ST.page(items, has_more=False)])
        list(c._list("/v1/customers", None))
        assert len(c._call_log) == 1

    def test_empty_data_with_has_more_true_terminates(self):
        """Defensive: even if has_more is True, empty data must stop the loop."""
        c = _ST.make_client([_ST.page([], has_more=True)])
        items = list(c._list("/v1/customers", None))
        assert items == []
        assert len(c._call_log) == 1


@pytest.mark.unit
class TestStripeStatusAllForSubscriptions:
    """`status=all` is passed for subscriptions so canceled subs are included."""

    def test_list_subscriptions_sends_status_all(self):
        c = _ST.make_client([_ST.page([])])
        list(c.list_subscriptions(None))
        params = c._call_log[0][1]
        assert params.get("status") == "all"

    def test_list_customers_has_no_status_param(self):
        c = _ST.make_client([_ST.page([])])
        list(c.list_customers(None))
        params = c._call_log[0][1]
        assert "status" not in params

    def test_list_invoices_has_no_status_param(self):
        c = _ST.make_client([_ST.page([])])
        list(c.list_invoices(None))
        params = c._call_log[0][1]
        assert "status" not in params


@pytest.mark.unit
class TestStripeEndpointPaths:
    """The correct Stripe API paths are used for each object type."""

    def test_list_customers_path(self):
        c = _ST.make_client([_ST.page([])])
        list(c.list_customers(None))
        assert c._call_log[0][0] == "/v1/customers"

    def test_list_subscriptions_path(self):
        c = _ST.make_client([_ST.page([])])
        list(c.list_subscriptions(None))
        assert c._call_log[0][0] == "/v1/subscriptions"

    def test_list_invoices_path(self):
        c = _ST.make_client([_ST.page([])])
        list(c.list_invoices(None))
        assert c._call_log[0][0] == "/v1/invoices"


@pytest.mark.unit
class TestStripeIncrementalCursorAndSinceInteraction:
    """The `created[gt]` filter + `starting_after` cursor work together correctly."""

    def test_since_and_pagination_coexist(self):
        """created[gt] is set on ALL pages (not just page 1) when since is given."""
        cust_a = _ST.customer("cus_A", created=1_749_000_000)
        cust_b = _ST.customer("cus_B", created=1_749_001_000)
        p1 = _ST.page([cust_a], has_more=True)
        p2 = _ST.page([cust_b], has_more=False)
        c = _ST.make_client([p1, p2])
        items = list(c._list("/v1/customers", "1748000000"))
        assert len(items) == 2
        # Both requests carry created[gt]
        assert c._call_log[0][1]["created[gt]"] == 1_748_000_000
        assert c._call_log[1][1]["created[gt]"] == 1_748_000_000
        # Second request carries starting_after
        assert c._call_log[1][1]["starting_after"] == "cus_A"
