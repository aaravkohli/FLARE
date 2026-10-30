# Isolated browser verification

Run from the repository root with the Python 3.11 `venv` and locked frontend dependencies installed:

```sh
FLARE_TEST_PRODUCTION=1 FLARE_TEST_MISSING_MODELS=1 \
  npm --prefix frontend-react run test:browser -- \
  adaptive-study.spec.ts resilience-controller.spec.ts autonomous-recovery.spec.ts mission-requests.spec.ts
```

Playwright creates a random operator password and SDN token once per invocation.
Workers and the disposable service harness inherit the same credentials. The
harness overrides inherited operator authentication settings only in its child
process environment; it does not change the running operator services or config.
It checks real login and authenticated mission access before exposing the
frontend listener. Model-unavailable tests remove weights only in the private copy.

For standalone review/profile tools, first start `scripts.swarm_test_services`.
Point `FLARE_TEST_CREDENTIALS_FILE` at `credentials.json` inside the harness's
reported private artifact directory before invoking the tool. The file has mode
0600 and is gitignored. Alternatively supply all three `FLARE_TEST_OPERATOR`,
`FLARE_TEST_PASSWORD`, and `FLARE_TEST_SDN_TOKEN` variables to both processes.
Test tools have no fallback to a checked-in password or the operator's account.
Do not publish the credentials file or retain test credentials for deployment.

Configuration helpers wait for successful backend responses and the UI's pending
state to finish. The control helper may simulate an explicit operator retry after
a **confirmed 409 rejection**. A timeout, lost response, or ambiguous delivery
fails without repeating the action. `mission-requests.spec.ts` tests this boundary
against real services with response fault injection.

The simulator service yields between complete fixed steps when draining its
wall-time budget. State, pause and configuration requests can therefore run
during catch-up without changing exact packet timestamps or dropping pending
simulation steps. Recovery scenario presets restore their registered fleet size;
the total-loss smoke intentionally retains all 30 UAVs at 4x speed. CI stores
service logs alongside browser evidence, excluding its private credentials file.

For credential isolation unit coverage, run `npm --prefix frontend-react test`
and `venv/bin/python -m pytest -q tests/test_browser_service_auth.py`.

GitGuardian scans commit history as well as the current files. Removing a
credential from the current source does not erase historical findings or update
an already-completed check. Existing findings require a fresh check and may need
review in GitGuardian; do not rewrite shared history or disable detection to
conceal them.
