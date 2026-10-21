# Rehearsal evidence and reproduction

The ten-slide deck uses the first recorded rehearsal. A second fresh production-stack rehearsal on 21 October 2026 verified the same five-hop latency (48.15747959331477 ms) and core lifecycle. Session/decision identifiers differ legitimately.

## Core route

`final-rehearsal/result.json` reports Passed, delivered peer probe, actual backend, learned-model sources and no browser page errors. `rehearsal.json` contains the captured authoritative snapshots. `probe-result.json` contains the exact five-hop packet result.

Execute against disposable services only:

```bash
FLARE_REVIEW_API=http://127.0.0.1:18400 \
FLARE_REVIEW_FRONTEND=http://127.0.0.1:15473 \
FLARE_ARTIFACT_DIR=/private/tmp/flare-panel-review-core \
node frontend-react/e2e/review-demo.mjs
```

Start those services in another terminal with the command in `DEMO.md`. The script changes and resets its designated mission. Its defaults target the earlier isolated ports 18200/15273, so set the variables explicitly for a new run. It uses installed Chrome on this Mac.

## Extended route

Run after core, while its disposable backend remains alive:

```bash
FLARE_REVIEW_API=http://127.0.0.1:18400 \
FLARE_REVIEW_FRONTEND=http://127.0.0.1:15473 \
FLARE_ARTIFACT_DIR=/private/tmp/flare-panel-review-extended \
node frontend-react/e2e/review-extended.mjs
```

`final-rehearsal/extended-result.json` records Passed for replay, 3D/camera/emphasis controls, paused altitude queue and acknowledgment, synchronized A/B/cancellation, the small ten-topology benchmark/JSON export and no page errors.

The small demonstration benchmark uses ten UAVs, seed 7, normal scenario, range 350 m, duration 10 and warmup 5. It is separate from the 30-UAV, three-seed research benchmark in `mesh-results.json`.

## One corrected rehearsal assumption

The first extended utility opened a new browser context and assumed the earlier browser's recorded probe would already be in its in-memory replay history. Its assertion failed with “No recorded probe outcome for this pair.” That is retained in `final-rehearsal/extended-initial-failure.json` rather than erased.

The corrected utility sends and observes a new probe in that browser before replaying it. It then passed. The demo guide keeps the main workflow in one tab. A successful rerun does not establish automatic restoration of old packet history in a fresh browser context.

## Isolation and boundaries

The private source/config/model copies and production frontend were owned by `/private/tmp/flare-panel-final-20261021`. Before/after fingerprints matched all 44 protected operator configuration/model/state files. The ordinary live services and their mission were not reset by these rehearsal actions.

These are software packet and logical SDN tests. They do not validate physical RF, flight, real satellite transport or real OpenFlow forwarding. The full application audit retains unresolved failures and blocked infrastructure gates, described in `RESULTS.md`.
