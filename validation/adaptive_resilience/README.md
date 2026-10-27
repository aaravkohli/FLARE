# FLARE Adaptive Resilience Study

This milestone adds controlled Auto-versus-fixed research jobs, effective peer
congestion, paired statistical summaries, and simulation-clock performance
visibility. All network results are **modeled packet simulation**. Live Mission's
FL/DQN/security pipeline and manual overrides remain separate from this study.

The [verification report](verification/README.md) records the completed 550-run
study, negative as well as positive Auto findings, tests, performance and remaining
limitations. Selected [per-run measurements](per-run-measurements.csv) and
[paired summaries](statistical-summaries.json) accompany it.

## Protocol

`adaptive_resilience_v1` has eleven arms: Auto (balanced) and all ten primary
fixed topologies. Fixed arms explicitly use Manual mode. Auto is a policy arm,
not an additional topology in the primary registry. Every arm uses the same
mesh-only, trust-aware shortest-path reference policy. Satellite transport,
ground-control transit, learned inference, and FL training are excluded.

| Preset | UAVs | Seeds | Scenarios | Runs |
|---|---:|---|---|---:|
| Exploratory pilot | 30 | 7, 42, 99 | All five below | 165 |
| Held-out evidence | 30 | 1001–1010 | All five below | 550 |

Cases are normal, mobility, regional jamming, congestion, and
partition/reconnection. The partition is a **policy-imposed link cut**, not
physical movement. Each run offers primary traffic over `[0,65)` simulation
seconds, measures packets created in `[5,65)`, and drains the twenty-second TTL:
85 simulation seconds in total. Primary traffic retains the existing first
fixed-step emission and subsequent one-second cadence.

Existing fixed comparison/benchmark duration semantics remain unchanged:
`duration_s` includes warmup, so the default sixty-second fixed workload has
fifty-five measured traffic seconds. The study's sixty measured seconds are
explicitly different. Immutable v1 requests reject overrides to seeds, fleet,
timing, scenarios, or protocol identity; a changed TTL requires a new protocol.

## Versioned workload

`mesh_peer_reference_v3` retains one 512-byte primary sensor packet per UAV per
second, addressed to its opposite-index peer. During `[8,25)` it adds five
deterministic peer flows, each offering 100 packets/s of 4096 bytes, in fixed-step
batches. All packets pass through the authoritative scheduler, airtime
reservations, queue limits, policy checks and TTL handling. Background offers
contribute to adaptive demand and contention, but not primary-cohort metrics.
An offered-load event is not a detector observation.

The legacy v2 congestion workload flooded Base, which the mesh-only policy
excludes, and therefore did not load the radio scheduler. Historical artifacts,
including `results/mesh_topology_benchmark_v2.*`, are not overwritten. Future
fixed jobs disclose and use v3. Unaffected fixed workload results are checked
against pre-change baselines.

Auto receives nominal opposite-peer demand before its initial evaluation.
Nominal demand is labeled an estimate, not observed traffic. Once offers exist,
the existing ten-second observation window is used; after the traffic window
and its observation history end, there is no invented reference demand.

## Inputs, isolation and reproducibility

Each submission copies configuration and fleet records into an isolated worker.
Identities, initial geometry, motion, primary/background offers, radio settings,
and scenario targets match across arms. Per-run input fingerprints include the
actual motion and offered-input streams, independently of scheduler admission.
Existing keyed channel draws are retained. Private resource-proxy sampling
restores selector hypothesis state and cannot alter subsequent adaptation.

No live registry, checkpoint, training state, federation files or mission clock
is written. Wall-clock measurements, job IDs and run timestamps are excluded
from scientific result hashes. Results retain separate source/configuration,
design, fleet, shared-input, offered-input, motion and packet-outcome hashes.

Traces stream directly to gzip. Storage checks use source-matched pilot artifact
sizes where available, otherwise a conservative initial estimate, plus reserve
space. Projections update after completed runs. A low-space guard stops before
exhaustion, with completed results retained. Cancellation and failures retain
partial results and explicitly identify incomplete traces.

## Analysis

The predeclared primary comparison is **Auto minus fixed Self-healing Mesh
delivery ratio**, by scenario. All other fixed-policy/metric comparisons are
exploratory. Each seed is one replication. Packets are never treated as
independent experimental replications.

Pairing requires matching scenario, seed, fleet, shared external inputs,
protocol, workload and design fingerprint. Incompatible pairs/collections and
duplicate observations are rejected. Mean paired differences use deterministic
95% percentile-bootstrap intervals with 10,000 seed-level resamples. Delivery
differences are percentage points. At least two valid pairs are required for an
interval; missing measurements stay null.

Latency, jitter and hops condition on delivered packets. Recovery durations
condition on recovered flows; unrecovered counts remain explicit. Components
are the existing final graph measurement. Resource cost is the whole-run mean
of equally weighted maintenance and battery-weighted transit utilization
proxies sampled once per simulation second. It is not calibrated energy.
Negative Auto results are valid findings; no universal winner is inferred.
Route convergence conditions on flows with a confirmed replacement route;
service recovery conditions on flows with a subsequent delivery. Their eligible
subsets can differ. `unrecovered_flows` counts missing service recovery. Intervals
are marginal per scenario/metric, without multiplicity adjustment.
Switch/failure/deferred counters cover the whole run. A successful initial Auto
selection counts as a completed transition; `initial_topology` is the active
graph immediately after engine initialization. Consult timestamped switch events
to distinguish startup selection from later adaptation. Timing diagnostics are
the latest bounded phase samples (which can fall in the quiet TTL drain), while
per-run CPU/wall totals cover the entire run.

## API and workspace

Use the existing authenticated `POST /mission/simulation/experiments`:

```json
{"mode":"adaptive_study","protocol_id":"adaptive_resilience_v1","preset":"pilot"}
```

Study batches support Run and Cancel. Existing compare/benchmark request shapes
remain accepted. Status/result polling, authorization and trace downloads reuse
existing endpoints. Exports accept `json`, `csv`, `md`, and `manifest`; filtering
the interface never changes export contents. Full JSON retains timing and
recovery detail; CSV provides per-run measurements; Markdown provides the
research summary; manifests contain configuration/provenance and artifact hashes.

Mission → Topology Benchmark → **Adaptive Study** keeps independent fixed-job
and study state while navigating. Charts retain individual seed observations.
Scenario filtering and baseline selection are distinct from experimental
configuration. Cancelled/failed jobs show partial retained results. A lost
submission receipt produces an explicit error and is never automatically sent
again. Experiment history and full mission replay are later milestones.

Live Mission shows achieved simulation seconds per wall second. View Settings →
Simulation performance contains bounded scheduling, rebuilding, selection and
snapshot timings, speed relative to the requested setting, and clipped wall
time. Paused/stale labels remain explicit. These diagnostics are separate from
rendering FPS and scientific simulation measurements.

## Reproduction

```sh
venv/bin/python -m pytest -q tests/test_adaptive_study.py tests/test_adaptive_topology.py tests/test_mesh_research.py tests/test_swarm_api.py tests/test_audit_simulator_matrix.py
npm --prefix frontend-react test
npm --prefix frontend-react run typecheck
npm --prefix frontend-react run lint
npm --prefix frontend-react run build
FLARE_TEST_PRODUCTION=1 FLARE_TEST_MISSING_MODELS=1 FLARE_ARTIFACT_DIR=/private/tmp/flare-study-browser npm --prefix frontend-react run test:browser -- adaptive-study.spec.ts
venv/bin/python -m scripts.run_adaptive_study --preset pilot --directory /private/tmp/flare-study-pilot
venv/bin/python -m scripts.run_adaptive_study --preset evidence --workers 3 --pilot-state /private/tmp/flare-study-browser/pilot-state.json --directory /private/tmp/flare-study-evidence
venv/bin/python -m scripts.verify_adaptive_study /private/tmp/flare-study-evidence
venv/bin/python -m scripts.profile_adaptive_topology --count 100 --seconds 10 --output /private/tmp/flare-study-cpu.json
FLARE_TEST_PRODUCTION=1 FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 FLARE_PROFILE_VIEW=2d FLARE_PROFILE_TOPOLOGY=auto npm --prefix frontend-react run test:browser -- mesh-performance.spec.ts
```

Repeat the final command in a fresh service process with `FLARE_PROFILE_VIEW=3d`
and, separately, `compare`. Sustained profiles and the full evidence study are
outside routine PR CI. CI runs frontend units and an isolated production browser
smoke with explicitly missing-model fixtures. Linux uses installed Playwright
Chromium; macOS retains the existing Chrome/Metal configuration.

If storage or an external interruption ends a batch, offline continuation can
retain its completed observations in a **new** directory:

```sh
venv/bin/python -m scripts.resume_adaptive_study /private/tmp/flare-study-interrupted /private/tmp/flare-study-continued --workers 2
```

Continuation requires identical numerical source and protocol and validates
each retained trace hash. The interrupted directory stays immutable. Only
missing scenario/seed/policy cases execute; exports identify the prior failure
and retained-run count. This is a local research recovery utility, not a new
pause/resume control or experiment-history feature in the application.
The application batch estimator uses a conservative global maximum trace size.
Offline continuation refines its projection using each policy's maximum observed
compressed bytes per offered packet and the remaining workload volume, with
50% headroom and 512 MiB reserve. The worker's low-space guard remains active.

The preserved execution used its archived numerical source. Subsequent recovery
serialization fixes are separately source-bound; old interrupted jobs intentionally
refuse continuation under changed source. Restore archived source into a disposable
copy when reproducing that execution, or start a new study on current source.
Raw execution artifacts are never rewritten by canonicalization.

Generated study artifacts stay under `results/adaptive_resilience/` locally;
large traces are excluded from Git. Selected verification summaries and evidence
manifests accompany this guide. Verification outcomes use only **Passed**,
**Failed**, **Blocked**, and **Not Tested**; application job lifecycle statuses
are separate.

The independent verifier checks every planned run, cross-arm external input
equality, cohort sizes, conservation, final TTL drain, effective congestion,
unrecovered counts and every compressed artifact hash. It also streams a
predeclared stratified sample (all eleven policies and five cases at the first
preset seed) to independently recompute primary delivery, latency, throughput,
packet-event ordering and outcome hashes. Network lifecycle records in JSONL
retain their original simulation times and may be flushed after packet records;
replay consumers should merge these streams by simulation time rather than
mistaking file flush order for network event order.
