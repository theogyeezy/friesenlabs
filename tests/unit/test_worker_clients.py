"""Unit: the worker builds its tool clients from env (shared/config names) and binds the tenant
per call — import-safe, nothing constructed at import, no AWS/Anthropic/psycopg2 in tests."""
import pytest

from worker import worker


@pytest.mark.unit
def test_worker_module_is_import_safe():
    # Importing the module constructed nothing live: the env-driven client builder is a function
    # called from run() only, and TOOLS are plain Tool instances (no clients inside).
    assert callable(worker.build_clients_from_env)
    assert callable(worker.run)
    assert worker.TOOLS  # the registered tool list exists without any client/network


@pytest.mark.unit
def test_build_clients_from_env_unconfigured_returns_none_clients(monkeypatch):
    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST",
                "CUBE_ENDPOINT", "CUBEJS_API_SECRET_VALUE"):
        monkeypatch.delenv(var, raising=False)
    clients = worker.build_clients_from_env()
    assert clients == {"db": None, "rag": None, "cube": None, "greenlight": None}


@pytest.mark.unit
def test_build_clients_from_env_wires_pg_clients_when_db_configured(monkeypatch):
    # Prove the env -> client wiring (names from shared/config.py) without touching Postgres:
    # the lazily-imported constructors are patched at their source modules.
    import api.control.greenlight as gl_mod
    import api.pg_clients as pg_mod

    seen = {}

    class FakeCrm:
        def __init__(self, dsn):
            seen["crm"] = dsn

    class FakeRag:
        def __init__(self, dsn):
            seen["rag"] = dsn

    class FakeStore:
        def __init__(self, dsn):
            seen["store"] = dsn

    monkeypatch.setattr(pg_mod, "PgCrmClient", FakeCrm)
    monkeypatch.setattr(pg_mod, "PgRagClient", FakeRag)
    monkeypatch.setattr(gl_mod, "PgApprovalStore", FakeStore)
    monkeypatch.setenv("UPLIFT_DB_URL", "postgresql://crm_app:x@db.local:5432/uplift")

    clients = worker.build_clients_from_env()
    assert isinstance(clients["db"], FakeCrm)
    assert isinstance(clients["rag"], FakeRag)
    assert clients["greenlight"].store.__class__ is FakeStore
    # All three rode the SAME crm_app DSN from env.
    assert seen == {k: "postgresql://crm_app:x@db.local:5432/uplift" for k in ("crm", "rag", "store")}


class _BindingCrm:
    """PgCrmClient stand-in: exposes .binding() handing out FRESH per-call adapters."""

    def __init__(self):
        self.handed_out = []

    def binding(self):
        b = _Bound()
        self.handed_out.append(b)
        return b


class _Bound:
    def __init__(self):
        self.tenant = None

    def set_tenant(self, tenant_id):
        self.tenant = tenant_id


@pytest.mark.unit
def test_build_context_derives_fresh_db_binding_per_call():
    crm = _BindingCrm()
    clients = {"db": crm, "rag": None, "cube": None, "greenlight": None}

    ctx_a = worker.build_context({"tenant_id": "tenant-A", "agent": "nadia"}, clients)
    ctx_b = worker.build_context({"tenant_id": "tenant-B"}, clients)

    # Fresh adapter per call — tenant state is never shared across concurrent tool calls.
    assert len(crm.handed_out) == 2
    assert ctx_a.db is not ctx_b.db
    assert ctx_a.tenant_id == "tenant-A" and ctx_a.agent == "nadia"
    assert ctx_b.tenant_id == "tenant-B"

    # bind_tenant (called by Tool.invoke) scopes each adapter to ITS call's tenant only.
    ctx_a.bind_tenant()
    ctx_b.bind_tenant()
    assert ctx_a.db.tenant == "tenant-A"
    assert ctx_b.db.tenant == "tenant-B"


@pytest.mark.unit
def test_build_context_passes_plain_clients_through():
    # A client without .binding() (fakes, future cube client) is used as-is — the original
    # build_context contract is unchanged.
    class PlainDb:
        def set_tenant(self, tenant_id):
            self.tenant = tenant_id

    db = PlainDb()
    ctx = worker.build_context({"tenant_id": "tenant-A"}, {"db": db, "rag": "r", "greenlight": "g"})
    assert ctx.db is db
    assert ctx.rag == "r" and ctx.greenlight == "g"


# --------------------------------------------------------------------------- cube client wiring
@pytest.mark.unit
def test_build_clients_from_env_wires_the_real_cube_client(monkeypatch):
    from agents.tools.cube_client import CubeClient

    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CUBE_ENDPOINT", "http://cube.local:4000")
    monkeypatch.setenv("CUBEJS_API_SECRET_VALUE", "test-secret")

    clients = worker.build_clients_from_env()
    assert isinstance(clients["cube"], CubeClient)
    assert clients["cube"].configured  # both pieces present -> queries can be signed AND sent


@pytest.mark.unit
def test_cube_endpoint_without_secret_degrades_visibly_not_silently(monkeypatch):
    """Misconfig (endpoint, no secret) shows up in the tool OUTPUT — never as a missing client
    that silently returns []."""
    from agents.tools.cube_client import CubeClient
    from agents.tools.readonly import QueryCube

    for var in ("UPLIFT_DB_URL", "DB_USER", "DB_PASS", "DB_HOST", "CUBEJS_API_SECRET_VALUE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CUBE_ENDPOINT", "http://cube.local:4000")

    clients = worker.build_clients_from_env()
    assert isinstance(clients["cube"], CubeClient)
    assert not clients["cube"].configured

    ctx = worker.build_context({"tenant_id": "tenant-A"}, clients)
    out = QueryCube().invoke(ctx, measures=["Deals.count"])
    result = out["result"]
    assert result["rows"] == []
    assert result["cube_status"] == "unconfigured"
    assert "CUBEJS_API_SECRET_VALUE" in result["detail"]


@pytest.mark.unit
def test_worker_query_cube_errors_loudly_when_cube_unreachable():
    """An unreachable Cube is an ERROR the agent can see (cube_status/detail) — never a silent
    empty rows list it would present as 'no data'."""
    from agents.tools.cube_client import CubeClient
    from agents.tools.readonly import QueryCube

    def refusing_transport(url, body, headers, timeout_s):
        raise OSError("connection refused")

    cube = CubeClient(endpoint="http://cube.local:4000", secret="test-secret",
                      transport=refusing_transport)
    ctx = worker.build_context({"tenant_id": "tenant-A"}, {"db": None, "cube": cube})
    out = QueryCube().invoke(ctx, measures=["Deals.count"])
    result = out["result"]
    assert result["rows"] == []
    assert result["cube_status"] == "error"
    assert "unreachable" in result["detail"]
