# infra/REQUESTS.md — Lane Matt → Lane Nick infra handoff queue

Lane Matt never edits `infra/**`, `db/roles.sql`, `.github/workflows/**`, or Dockerfiles
(see `CONTRIBUTING.md` § Two-lane contract). Anything the app needs from infra is **appended here**
as a request block; Lane Nick implements, `terraform validate`s, applies, and checks it off — in order.

## Request format

```markdown
### REQ-<NNN>: <one-line summary>
- **Status:** OPEN | IN-PROGRESS (Nick) | DONE @<sha> | REJECTED (<reason>)
- **Requested by:** Lane Matt @<sha or PR#>
- **Needed for:** <TODO item / feature>
- **Env/secret names** (must already exist in shared/config.py): <names or n/a>
- **Spec:**
  ```hcl
  # exact resources / variables (safe "" defaults) / outputs — or fenced SQL for GRANTs
  ```
- **Done when:** <verifiable condition>
```

Rules:
- Append-only for Matt; only Nick edits Status lines.
- Every new terraform variable carries a safe `""`/count-0 default so `validate` and the deploy
  pipeline plan stay green before the value exists.
- New env-var or secret NAMES land in `shared/config.py` first; requests reference them, never invent.
- GRANT requests: fenced SQL here, tests GRANT in fixtures — `db/roles.sql` stays Nick-only.

---

## Queue

_(no open requests)_
