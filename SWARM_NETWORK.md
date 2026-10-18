# FLARE mesh network laboratory

The mission workspace uses the authoritative Python swarm service. The 2D canvas
and retained Three.js world show the same packet-event timeline. No renderer
creates packet outcomes or contributes network metrics.

## Run and inspect

Use the existing `run_local_simulation.sh` launcher with `MODE=simulation` and
`FLARE_SWARM_ENABLED=1`. The Mission view opens in 2D. Select a topology, inspect
source/destination routes, and send a probe. Changing topology preserves flight
positions; **Apply suggested formation** sends the existing delivery/acknowledgment
based flight command. Pause, reset, range, fleet size and speed remain server-owned. A changed seed is shown as pending until reset; the displayed active seed continues to describe the current packet draws.
The 0.1× setting and packet replay expose short hops without changing hop durations.

The model services are optional for network experiments. Their absence is shown
as unavailable. A main-mission FLARE decision still uses the deployed DQN's
existing direct/satellite/mesh/HOLD action space. The network engine computes hops;
logical SDN confirms installation. Injected attacks are condition ground truth,
not detector verdicts. Trust and containment observations retain their existing
FL/orchestrator source. There is no new checkpoint format or training requirement.

## Live mission controls

The toolbar keeps Play/Pause, speed, topology and 2D/3D view selection visible.
**Mission setup** contains fleet size, formation, radio range, scenario presets and
reset. **Route probes** expands source/destination inspection; packet replay and
layer controls sit directly above the canvas.

The **Simulation event** panel is always visible alongside the desktop canvas and
below it on narrow screens. Communication profiles retain the original names:
Normal, Spot jam, Sweep, Barrage, Smart jam, Reactive, Adaptive, FHSS, Spoofing,
GPS spoofing, Replay and DoS. Insider traffic and FL poisoning are separate
categories. Defaults are inspected UAV, Direct, and 15 simulation seconds.
Choose selected UAVs or the entire fleet explicitly; the target identities are
captured when sending. The duration slider covers 5–60 seconds and numeric input
accepts 1–300. Multiple route families are available under an advanced disclosure.
Normal clears the chosen category for the chosen scope. Individual clear and
clear-all actions do not erase detector history or release containment. Conditions
and their countdowns use simulation time, including when paused. Failed or
uncertain submissions show an inline error and never automatically repeat.

**Inspect / Commands** separates grouped network, security and flight telemetry
from command-target selection. Below the canvas, Direct / Satellite / Mesh show
selected-UAV measurements and the detection, policy, safety and SDN pipeline.
Those panels consume existing canonical telemetry and transition events, gated by
UAV and session identity. Pending requests retain the last confirmed installation;
stale or disconnected evidence is labeled as retained. Current simulator hops are
shown separately from model-family decisions. Injecting an attack never fabricates
a detection, successful installation or attribution. Escape exits presentation
mode and restores focus; reduced motion retains static packet indicators.

## Topologies and physical constraints

The ten primary choices are full mesh, partial mesh, star, ring, tree, cluster
mesh, grid, random geometric, hybrid mesh, and dynamic self-healing mesh. Legacy
`chain` and `manet` configurations remain accepted by the backend.

- Full mesh uses every eligible UAV pair; partial mesh takes the union of four
  nearest-neighbor choices (the undirected degree can exceed four).
- Ring/tree/grid relationships use stable fleet slots. A failed member leaves a
  structural hole rather than connecting its former neighbors automatically.
- Star and cluster heads remain stable until failure, quarantine, or an explicit
  higher-priority role. Failed-head reelection waits the configured discovery
  timeout. Clusters use stable groups of up to ten fleet slots.
- Random geometric uses a logical radius of 65% of endpoint radio range.
- Hybrid combines head/member hierarchy, head connectivity and local peer links.
- Self-healing retains eligible peers and adds the shortest physically eligible
  edges needed to join forwarding-eligible components. Blocked/jammed edges and restricted/held/unsafe relays are excluded from component repair; control-only peer links remain available to control traffic. A real
  partition remains partitioned until a feasible connection returns.

Every chosen edge passes the modeled radio check. Range is the minimum endpoint
range. Ground control can be a route endpoint, never a hidden transit node between
UAVs. Discovery is a sampled-neighbor approximation at the network interval,
not an implementation of a specific wireless discovery protocol.

Radio defaults are in `config/swarm_config.yaml`: 350 m range, 12 Mb/s nominal
bandwidth, log-distance exponent 2.2, −36 dBm reference RSSI at 1 m, −100 dBm
noise, and an 8 ms abstract base link delay plus distance/c. The base delay
represents modeled processing/access delay, not free-space propagation alone.
RSSI, SINR, terrain obstruction and regional jammer noise feed an approximate
sigmoid delivery model. This is not a calibrated Wi-Fi PHY. The log-distance
form is described in the [ns-3 propagation model documentation](https://www.nsnam.org/docs/models/html/propagation.html).

## Packet clock, traces and metric definitions

Movement advances in 50 ms steps; radio discovery is sampled every 200 ms. Packet
starts, serialization, arrivals, retries and expiry use an ordered event queue
with precise simulation timestamps inside those steps. Sharing either endpoint
radio contends for half-duplex serialization airtime. Separate radios can transmit
concurrently. Bounded queues and the existing TTL/capacity/retry limits apply.
Jitter is a bounded uniform addition (default 0–2 ms). Configured seeds produce
keyed loss/jitter draws, independent of rendering or random-number consumption.
Reliable command/ack/FL traffic may repair the unfinished suffix of a broken route;
completed hops remain in its history. Unreliable traffic drops on an invalid route.

The frontend buffers 250 ms of wall time in simulation units and never extends an
individual hop. A delivery/drop/retry ring is a short event indicator. The 2D view highlights the selected UAV’s confirmed FLARE telemetry path separately from the inspected peer route; satellite traffic uses an explicitly labeled transport ribbon rather than an invented radio edge. Packet
replay uses recorded timestamps at 0.1×. Reduced motion displays static indicators;
paused/stale evidence does not extrapolate. Rendering caps at 200 indicators;
accounting remains complete. Live traces retain 50,000 events; the client retains
up to 20,000 events within 30 simulated seconds. Retention gaps are explicit.

Main mission counters cover the whole session. Live delivery ratio is delivered /
(delivered + dropped), with pending packets accounted separately. End-to-end latency
includes queueing and retries. Throughput counts delivered payload bits per elapsed
simulation second. Connected components describe data-forwarding eligibility, including isolated held/restricted UAVs, and exclude ground control. Available radio edges and endpoint forwarding permissions are separate concepts. Estimated RF
features used by existing model inputs remain distinct from observed packet
outcomes; neither is presented as field measurement.

## Comparisons and benchmarks

A/B and batch jobs run in child processes. They use read-only copies of fleet
records, private engines, no live registry writes, no FL training or Byzantine
control-file effects, and no orchestrator inference. The same seed, distributed
geometry, movement, per-node 512-byte peer traffic at 1 Hz, and condition schedule
are used for every topology. Destinations are offset by half the fleet. Routing
uses the same deterministic trust-aware shortest-path policy; ground/satellite
shortcuts are excluded. A/B time, camera and selected identity are synchronized.

The ten scenario presets are normal, high density, mobility, single failure,
multiple link failures, regional jamming, compromised relay, self-healing, congestion,
and partition/reconnection. Injected spatial interference is shown as a labeled region in 2D and a sphere in 3D, separately from detector verdicts. Conditions begin at 8 s and recover at 25 s; normal
and high density have no injected condition. Benchmark targets use the same
identities/endpoint pairs rather than choosing a different vulnerable node for each
topology. Compromised relay behavior drops actual packets without inventing a
model verdict. Partition/reconnection uses an explicit bilateral link cut.
Legacy scenarios, including poisoning through actual Flower dispatch, remain usable
in the main mission. Reference comparisons do not claim FL detection performance.

Benchmark defaults: 30 UAVs, seeds 7/42/99, 60 s of offered traffic, a 5 s warmup,
and a final packet-TTL drain. Only reference packets created in [warmup, duration)
contribute to cohort metrics, including packets with no route. Latency and hop
means cover delivered packets; missing samples export as null. Jitter is the mean
absolute change in delivered latency within each flow. Throughput divides delivered
cohort bits by the measurement interval. Recovery records distinguish route
convergence from the first successful delivery after disruption. Convergence starts at observed route invalidation; logical installation is instantaneous in this approximation and can produce 0 ms. Service recovery includes subsequent packet delivery and traffic cadence; absent recovery
is left unset. Final connected components describe the final graph, not a temporal
average. JSON includes raw per-seed results, full base simulation configuration (request fields override it), input fleet records, terrain provenance, simulator source/configuration digests and a results digest;
CSV has per-run metrics. Gzipped JSONL traces retain all experiment packet events.

## Additive API

Authenticated browser routes are under `/mission/simulation`; upstream service
routes use `/simulation` and the existing SDN bearer token.

| Endpoint | Purpose |
| --- | --- |
| `GET /topologies` | Primary topology labels, descriptions and suggested formations |
| `PATCH /config` | Existing revision-checked config plus `range_m` and speed `0.1` |
| `GET /routes?source=…&destination=…&traffic_class=sensor` | Candidate versus installed route; no policy installation |
| `POST /probes` | Session/revision-checked `{source,destination,size_bytes}` probe |
| `GET /packet-events?after=…&limit=…` | Ordered cursor page, latest cursor and retention gap |
| `POST /experiments` | Create `compare` or all-ten `benchmark` job |
| `GET /experiments/{id}` | Progress, status, A/B snapshots or cohort results |
| `POST /experiments/{id}/control` | Play/pause/reset/cancel comparison; cancel benchmark |
| `GET /experiments/{id}/export?format=json\|csv` | Authenticated result download |
| `GET /experiments/{id}/traces/{filename}` | Completed job trace download |

Creation and controls remain admin-only in simulation mode. Reads retain existing
user authentication. Packet events also arrive through authenticated WebSocket
`mission_packet_events` batches, alongside backward-compatible `mission_snapshot`
messages. WebSocket auth may include `packet_session_id` and `packet_cursor` to resume missed packet events. A separate received cursor survives newer snapshots; per-client retention gaps remain visible until reset. Session identity and cursor deduplication prevent mixing resets.
Artifacts live beside the private mission registry in `experiments/`, or under
`FLARE_EXPERIMENT_DIR`. At most two workers run concurrently. Shutdown terminates
owned workers; interrupted jobs retain artifacts and report an interrupted failure.

## Validation

```sh
venv/bin/python -m pytest -q
cd frontend-react
npm test
npm run typecheck
npm run lint
npm run build
npm run test:browser
FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 npm run test:browser -- mesh-performance.spec.ts
```

The sustained profile measures 100-UAV full mesh in 2D and 3D, then two synchronized
100-UAV reference meshes. It saves hardware, frame distributions, retained JS heap
samples and screenshots. The FPS targets are ≥30 mean and ≥20 fifth percentile
on the recorded machine. A 60-second check is not a long-duration leak proof;
set a longer duration for soak testing. Existing lint warnings and the Three.js
bundle-size advisory are reported separately from test failures.

## Recorded verification

On 18 October 2026: 439 Python tests and 116 frontend tests passed, with successful
type checking, lint and production build. All 11 production browser tests passed;
subsequent focused checks covered reconnect cursors, spatial jamming, camera reset,
exports, and rendering after the final changes.

A 60-second headless Chrome profile on an Apple M2 (1440 × 1000) measured:

| 100-UAV view | Mean FPS | Fifth percentile FPS |
| --- | ---: | ---: |
| Full mesh, 2D | 58.1 | 59.5 |
| Full mesh, 3D | 54.3 | 30.0 |
| A/B partial and self-healing meshes, each pane | 60.0 | 59.9 |

Later focused profiles also passed the same thresholds. These are local rendering
measurements, not device-independent guarantees or RF validation.

The [reference JSON](results/mesh_topology_benchmark_v2.json) and
[CSV](results/mesh_topology_benchmark_v2.csv) contain the actual default benchmark:
30 UAVs, ten topologies, seeds 7/42/99, 60 seconds of offered traffic, five seconds
of warmup, and a TTL drain. Each of its 30 runs accounts for 1,650 reference cohort
packets. A second independent run produced the identical results digest. Packet
conservation passed in every run. Large validation traces are not checked in; the
replay command regenerates them from the recorded inputs.

```sh
venv/bin/python -m scripts.replay_mesh_benchmark results/mesh_topology_benchmark_v2.json
```

This command refuses a changed simulator source, uses an empty temporary output
directory by default, and verifies the regenerated results digest. Browser JSON
benchmark exports carry the same inputs and can be supplied instead.
