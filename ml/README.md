# ml/ — Cortex (per-tenant models)

Per-tenant models that learn from the tenant's own data — the "your model, your data" claim. The
flywheel: more usage → more labeled outcomes → better per-tenant models → stickier product.

## Pipeline
1. `features.py` — build a numeric feature matrix + label (lead → booked job) from tenant records.
2. `train.py` — split → train candidates (a **bake-off**) → evaluate on a held-out split → pick the
   best by held-out AUC. Deterministic given `seed`.
3. `estimator.py` — the `Estimator` protocol. Offline ships a real pure-Python `LogisticRegression`
   (+ a `MajorityBaseline` floor) so the whole pipeline tests with no heavy deps. **Production swaps in
   LightGBM/XGBoost** (same protocol) on SageMaker/Modal — tabular training is light, schedule it,
   don't keep GPUs warm.
4. `registry.py` — per-tenant model registry (versions + metrics) + the **champion/challenger gate**:
   a challenger promotes only if it beats the incumbent on held-out AUC by a margin.
5. `run_model` tool (`agents/tools/run_model.py`) — agents call the tenant's **champion** to score a
   record (e.g. Scout scoring a lead). Tenant-scoped: a tenant only ever sees its own model.
6. `retrain.py` — `retrain_tenant` (train + gate a challenger) and `drift_check` (flag when the live
   champion degrades beyond tolerance). The **EventBridge schedule** is authored in
   `infra/modules/cortex` (rate(7 days)); the training target is attached at apply time.

## Status
Pipeline + registry + gate + serving + drift are built and tested offline (the learner genuinely
learns: held-out AUC > 0.7 on synthetic separable data; gate + tenant-scoping + drift proven). Live
training on SageMaker/Modal and the EventBridge target are BLOCKED: needs Nick (cost/creds).

## Test
```bash
pytest tests/unit/test_ml_train.py tests/unit/test_ml_registry.py \
       tests/unit/test_ml_run_model_and_retrain.py -q
```
