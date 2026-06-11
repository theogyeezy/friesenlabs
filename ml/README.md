# ml/ — Cortex (per-tenant models)

Per-tenant models that learn from the tenant's own data — the "your model, your data" claim. The
flywheel: more usage → more labeled outcomes → better per-tenant models → stickier product.

## Pipeline
1. `features.py` — build a numeric feature matrix + label (lead → booked job) from tenant records.
   Nine features: the five raw fields (amount, n_activities, days_since_created, has_email, has_phone)
   plus derived signal (log-amount, engagement velocity, recency flag, contact-completeness). The
   vector is built only from fields BOTH the training loader and `run_model` inference produce, so
   train/serve parity holds by construction. APPEND-ONLY — the registered estimator is dimensioned to
   this vector.
2. `train.py` — split → train candidates (a **bake-off**) → evaluate on a held-out split → pick the
   best by held-out AUC. Deterministic given `seed`.
3. `estimator.py` — the `Estimator` protocol with **two real pure-Python learners**: a
   `LogisticRegression` and a `GradientBoostedTrees` (logistic-loss GBDT over shallow CART trees —
   captures the feature interactions logreg can't), floored by a `MajorityBaseline`. No heavy deps /
   GPUs, so the whole pipeline tests offline; the bake-off picks the winner per tenant on evidence. A
   future LightGBM/XGBoost candidate drops in behind the same protocol.
4. `registry.py` — per-tenant model registry (versions + metrics) + the **champion/challenger gate**:
   a challenger promotes only if it beats the incumbent on held-out AUC by a margin.
5. `run_model` tool (`agents/tools/run_model.py`) — agents call the tenant's **champion** to score a
   record (e.g. Scout scoring a lead). Tenant-scoped: a tenant only ever sees its own model.
6. `retrain.py` — `retrain_tenant` (train + gate a challenger) and `live_drift_check` (flag when the
   champion's live AUC degrades beyond tolerance). `drift_alert.py` publishes a positive drift verdict
   to the Cortex drift SNS topic so an operator is paged (inert without `CORTEX_DRIFT_TOPIC_ARN`). The
   **EventBridge schedule + Fargate retrain target + signing-key secret + drift topic** all live in
   `infra/modules/scheduled_jobs` (DISABLED until `cortex_retrain_enabled` flips).

## Status
Pipeline + registry + gate + serving + drift + drift-alerting are built and tested offline (the
learners genuinely learn: held-out AUC > 0.7 on linear synthetic data, and the GBT out-separates
logreg on an interaction pattern; gate + tenant-scoping + drift proven). Live activation — S3
registry, signing-key value, retrain schedule, a drift-topic subscription, and a first seeded
retrain — is owner-gated (cost/creds); steps in `GO_LIVE_CHECKLIST.md`.

## Test
```bash
pytest tests/unit/test_ml_train.py tests/unit/test_ml_registry.py \
       tests/unit/test_ml_run_model_and_retrain.py -q
```
