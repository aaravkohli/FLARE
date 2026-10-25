# Adaptive topology selection

Live Mission defaults to **Auto (balanced)**. The topology dropdown can pin any
primary topology until Auto is explicitly selected again. A topology-only API
configuration request remains a manual override. Legacy `chain` and `manet`
remain accepted. Comparisons and benchmarks explicitly set `topology_mode=manual`.
No new endpoint or learned model is involved.

## Decision evidence

`simulation/swarm/adaptive_topology.py` evaluates the ten registered primary
topologies at initialization and once per simulation second. Policy defaults
are validated from `config/swarm_config.yaml`. Pausing stops observation,
transition, cooldown and blacklist clocks.

Candidates use the same current radio eligibility and private copies of node,
cluster, leader and neighbor state. Hypothetical head discovery is retained
separately for each topology. No random draws or live routing changes occur
during evaluation. Availability, radio impairment, blocked edges, containment,
HOLD, threat and flow restrictions constrain scored routes. Ground control is
an endpoint and never bridges otherwise disconnected UAV components.

Candidates that lose an incumbent's reachable peer pair, control endpoint, or
installed/offered flow are inadmissible. Connectivity is compared before scores.
The remaining candidates use:

| Utility | Weight | Calculation |
|---|---:|---|
| Reliability | 35% | Traffic-weighted predicted path delivery, multiplied by 0.8 + 0.2 when an edge-disjoint alternative exists |
| Latency | 25% | Traffic-weighted `1 / (1 + predicted_delay_ms / 50)` |
| Capacity | 25% | Traffic-weighted serviceable demand under the busiest shared radio, allowing 20% headroom |
| Efficiency | 15% | One minus the mean of link-maintenance cost and maximum battery-weighted transit utilization |

The maintenance cost is `min(1, UAV edges / (8 × available UAVs))`. The relay
cost is `min(1, transit airtime utilization / max(0.01, remaining battery))`.
These are resource proxies, **not calibrated energy measurements**. Capacity
counts transmit and receive serialization on the existing half-duplex radio.

Demand is the actual offered mesh byte rate over the last ten simulation
seconds, including failed/deferred offers. Offers at a shared timestamp are
aggregated rather than evicted under congestion. Before traffic exists, the
existing nominal mission schedule supplies estimated demand. Idle installed
policies constrain reachability with zero extra demand. Direct and Satellite
measurements do not enter mesh hop estimates.

An edge with at least eight mesh attempts in the ten-second window uses observed
delivery and successful hop latency; other edges use the radio model. Modeled
delay includes nominal 512-byte serialization and mean configured jitter.
Counterfactual scores remain predictions, especially for unobserved links.
Diagnostics identify measured and estimated edge counts separately. The
existing bounded packet observation buffer remains the measurement source.

A candidate loses up to 0.05 score for the symmetric changed-edge fraction.
Initialization has no transition penalty because no packets exist. Equal
connectivity and scores favor the incumbent, then registry order.

## Stability and transitions

Normal changes require at least 0.08 net improvement, three consecutive wins,
and fifteen simulation seconds since the last committed change. An achievable
control-route loss or ten-percentage-point peer connectivity deficit must
persist over two rebuilds before an emergency change. Recovery must improve
connectivity; emergency attempts are limited to one per two simulation seconds.
Failed targets are excluded from automatic selection for thirty simulation
seconds. Physical partitions and security restrictions cannot be repaired by
changing topology alone.

Automatic and manual changes share one coordinator:

1. Prepare the graph, leader state and reachable policies privately.
2. Drain existing mesh packets for at most one simulation second. New mesh
   packets remain in the scheduler as deferred packets. Global capacity,
   source queue bounds and original TTL still apply. Direct and Satellite
   packets continue normally.
3. Re-prepare at the network boundary, checking automatic connectivity guards
   again. Commit graph and forwarding synchronously, then release deferred
   packets using current authoritative policy checks.
4. Validate forwarding over two subsequent rebuilds. A rejected or raised
   installation failure restores the previous topology against current radio
   conditions. If it cannot provide the best achievable connectivity, a bounded
   fallback transition is attempted. Failures and fallback remain visible.

Neither rollback nor cancellation rewinds time, counters, completed hops or
packet outcomes. At the drain deadline, existing unreliable packets can drop;
reliable packets use the existing bounded suffix repair. A configuration change
cancels pending automatic intent; an unrelated setting retains a pending manual
pin. Reset clears selector history while preserving mode and manual choice.
Scenario loading also preserves the manual pin.

Snapshots retain `topology` as the actually active graph. The additive
`topology_selection` field exposes mode, explanation, evaluation/commit times,
requested target, drain/validation state, cooldown, backoff and ten bounded
diagnostics. Routine evaluations do not change management revision. Transition
mutations do; existing authenticated revision checks remain in force.

## Interface

The toolbar displays Auto/manual intent. The line below it always shows the
active topology and backend explanation, with a separate requested target
during a transition. Mission Setup → **Topology selection details** contains
utilities, connectivity, observation provenance and cooldown/backoff state.
Stale snapshots are labeled last-confirmed rather than presented as new choices.
The 2D and 3D renderers continue consuming the same authoritative links/flows.

## Reproduction

```sh
venv/bin/python -m pytest tests/test_adaptive_topology.py tests/test_swarm_api.py tests/test_mesh_research.py tests/test_swarm_simulation.py tests/test_swarm_controls.py tests/test_audit_simulator_matrix.py -q
npm --prefix frontend-react test
npm --prefix frontend-react run typecheck
npm --prefix frontend-react run lint
npm --prefix frontend-react run build
FLARE_TEST_PRODUCTION=1 npm --prefix frontend-react run test:browser -- adaptive-topology.spec.ts mesh-research.spec.ts connections-countdown.spec.ts
FLARE_TEST_PRODUCTION=1 FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 FLARE_PROFILE_VIEW=3d FLARE_PROFILE_TOPOLOGY=auto npm --prefix frontend-react run test:browser -- mesh-performance.spec.ts
venv/bin/python -m scripts.profile_adaptive_topology --count 100 --seconds 10 --output /private/tmp/adaptive-overhead.json
```

Browser tests launch disposable services on 15173/18000/18080 and require local
port and Chrome permissions. They do not attach to the operator's 5173 mission.
Existing operator services were left running. Restart the API and simulator
when ready to load the new backend code; a simulator restart starts a new mission
session.

## Executed validation

See [evidence.json](evidence.json) for source hashes, counts, measurements and
local artifact locations. These checks cover this feature; this is not a new
whole-application audit or physical-network validation.

| Check | Outcome | Evidence |
|---|---|---|
| Ten candidates × sizes 10–100 × seeds 7/42/99 | Passed | 300 deterministic evaluations, graph eligibility and live-state/random-stream isolation |
| Existing topology initialization matrix | Passed | 300 topology/size/seed cases, including structural failure invariants |
| Configured full Python suite during implementation | Passed | 937 tests; executed in a disposable source/config/model copy before final selector refinements |
| Final affected Python regressions | Passed | 236 tests across adaptive policy, API, packets, commands, attacks, routing and model-family contracts; final selector-only rerun also passed 59 tests |
| Frontend unit tests | Passed | 171 tests, including stale evidence, manual intent and missing timestamps |
| Type checking, lint, production build | Passed | Seven pre-existing lint warnings and the existing large-chunk build warning remain |
| Final production browser workflows | Passed | 13 workflows: Auto/manual, reset, pause, both renderers, probes/replay, attack lifecycle, comparisons and benchmark export |
| Fixed-topology default benchmark vs pre-change implementation | Passed | All 30 runs have exactly equal numerical cohort/recovery measurements: 30 UAVs, seeds 7/42/99, 5-second warmup, 60 seconds traffic, 20-second TTL drain |
| Operator artifact isolation | Passed | Protected configuration/model/state fingerprints unchanged; disposable service listeners shut down after tests |

The final integration regressions specifically cover a topology validation pass
attempting to restore a superseded mesh family after DQN selected Satellite,
preservation of pending manual intent across an unrelated configuration change,
and committing the self-healing graph that was actually prepared. Installation
rejections and raised adapter failures are explicit fault-injection tests, not
claims of real OpenFlow/hardware failure coverage.

### Rendering and CPU cost

Separate fresh production sessions, 100 UAVs, 60 seconds per measurement, 1440 ×
1000 viewport, headless Chrome 155, Apple M2 with ANGLE Metal:

| Run | Average FPS | Fifth-percentile FPS | Observed simulation clock |
|---|---:|---:|---|
| Auto, 2D | 58.87 | 59.52 | T+1.7 → T+37.6 |
| Auto, 3D | 55.10 | 30.03 | T+0.8 → T+26.9 |
| Manual Grid, 3D | 54.03 | 30.03 | T+1.8 → T+46.7 |

All pass the 30-average / 20-fifth-percentile rendering thresholds. The renderer
buffer remained bounded and no browser errors were recorded. These are FPS
measurements, **not real-time simulation guarantees**. The 100-UAV simulation
clock advanced slower than wall time; the table reports its actual progression.
The profiles preceded the final backend contract guards; those guards have
separate final Python/API/browser regressions and do not change rendering.

The isolated CPU comparison starts both modes with the same selected Grid
topology, 100 UAVs, seed 42 and ten simulation seconds. Manual used 7.419 CPU
seconds; Auto used 11.985 CPU seconds (1.62×). Nine selector evaluations consumed
2.618 CPU seconds in total, roughly 253–405 ms each on this run. CPU cost varies
with graph density and workload. No performance claim is made for hardware
networks, other machines, or real-time execution at every fleet size.

`pytest.ini` excludes the script-style adversarial suite from the configured
Python run. Hardware, Ryu/Mininet and ns-3 gates were not part of this feature's
verification. Scoring uses the existing modeled radio/channel approximation and
resource proxies; it does not prove field reliability, energy use, or an optimal
topology for every continuous condition.
