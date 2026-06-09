"""Unit: per-tenant registry + champion/challenger gate."""
import pytest

from ml.registry import InMemoryRegistry, evaluate_and_gate


class DummyModel:
    def predict_proba(self, X):
        return [0.5 for _ in X]


def _register(reg, tenant, auc):
    return reg.register(tenant, "logreg", {"auc": auc, "accuracy": auc}, DummyModel())


@pytest.mark.unit
def test_first_model_becomes_champion_if_beats_random():
    reg = InMemoryRegistry()
    good = _register(reg, "t1", 0.8)
    assert evaluate_and_gate(reg, "t1", good) is True
    assert reg.champion("t1").version == good.version


@pytest.mark.unit
def test_first_model_not_promoted_if_no_better_than_random():
    reg = InMemoryRegistry()
    weak = _register(reg, "t1", 0.5)
    assert evaluate_and_gate(reg, "t1", weak) is False
    assert reg.champion("t1") is None


@pytest.mark.unit
def test_challenger_promotes_only_if_beats_champion_by_margin():
    reg = InMemoryRegistry()
    champ = _register(reg, "t1", 0.80)
    evaluate_and_gate(reg, "t1", champ)
    # A marginally-better challenger (within noise) does NOT promote.
    noise = _register(reg, "t1", 0.805)
    assert evaluate_and_gate(reg, "t1", noise) is False
    assert reg.champion("t1").version == champ.version
    # A clearly-better challenger promotes and demotes the old champion.
    better = _register(reg, "t1", 0.90)
    assert evaluate_and_gate(reg, "t1", better) is True
    assert reg.champion("t1").version == better.version
    assert champ.is_champion is False


@pytest.mark.unit
def test_registry_is_tenant_scoped():
    reg = InMemoryRegistry()
    evaluate_and_gate(reg, "t1", _register(reg, "t1", 0.8))
    assert reg.champion("t2") is None
    assert len(reg.versions("t1")) == 1
    assert reg.versions("t2") == []
