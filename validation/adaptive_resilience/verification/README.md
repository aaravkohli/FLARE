# FLARE Adaptive Resilience Study — verification report

The first research-readiness milestone is implemented, and **550/550 unique evidence runs completed**. This does not declare the entire application universally passing. Historical execution failures, remaining clock lag, and unexecuted remote/hardware gates are prominent below.

## Study and primary findings

Protocol `adaptive_resilience_v1`: 30 UAVs, held-out seeds 1001–1010, five scenarios, eleven policies. Fixed arms remain Manual. Auto is a policy arm, not an extra topology. Timing is 5s warmup + 60s measured traffic + 20s TTL drain = 85 simulation seconds/run.

The study offered **2,007,500 packets**, including **990,000 measured primary-cohort packets**. All completed runs conserve packets and drain pending packets. Cross-arm fleet, motion, offers and shared-input hashes match in each scenario/seed group. The compressed traces total **1.043 GiB**.

Primary comparison: **Auto minus fixed Self-healing Mesh delivery**, in percentage points. Each scenario has ten valid seed pairs and 10,000 deterministic paired percentile-bootstrap resamples.

| Scenario | Valid pairs | Mean difference (pp) | 95% interval (pp) |
|---|---:|---:|---|
| normal | 10 | -2.911 | -3.122 to -2.672 |
| mobility | 10 | +1.611 | +0.800 to +2.411 |
| regional_jamming | 10 | -1.972 | -2.211 to -1.694 |
| congestion | 10 | -3.244 | -3.544 to -2.878 |
| partition_reconnection | 10 | -1.789 | -2.022 to -1.533 |

Auto improves modeled delivery in mobility and has lower modeled delivery in the other four cases. These are valid negative results. Selector weights and thresholds were not tuned against the held-out seeds. This is not a universal topology ranking, an evaluation of the combined live FL/DQN workflow, or hardware evidence. Intervals are marginal per scenario without multiplicity adjustment; secondary comparisons are exploratory.

Packets are not independent replications. Latency/jitter/hops condition on delivered packets. Route convergence conditions on replacement-route confirmation; service recovery on subsequent delivery. Nulls and unrecovered flows remain explicit. Components are the existing final graph measurement. Switching/deferred counters cover the whole run, including initial Auto selection. Resource cost is an uncalibrated maintenance/relay proxy.

## Implemented capability coverage

The [coverage matrix](../feature-coverage.csv) maps implementation, interface applicability, evidence and outcome. This is the milestone inventory, not a claim that unrelated backend utilities require frontend controls.

- Adaptive Study sits inside Topology Benchmark, with independent fixed/study state and existing workspace navigation preserved.
- Additive authenticated experiment request, isolated execution, Run/Cancel, progress/current case, partial results, per-seed charts, paired summaries and row details.
- Complete JSON, per-run CSV, generated Markdown, manifests and compressed trace exports; display filters do not change export contents.
- `mesh_peer_reference_v3` retains primary opposite-peer 512-byte/UAV/second traffic. During [8,25), five peer flows offer 100 packets/s each at 4096 bytes through the actual scheduler. Background load contributes to contention and adaptive demand but not primary-cohort metrics.
- Nominal reference demand exists before Auto initialization and is labeled estimated. Partition/reconnection is a policy-imposed link cut. No detector verdict is manufactured.
- Live Mission shows achieved simulation speed; View Settings shows bounded scheduling/rebuilding/selection/snapshot timings and clipped wall time.
- PR CI adds frontend units and isolated production browser smoke, including explicit missing-model fixtures and portable Linux Chromium configuration.

Existing fixed benchmark duration semantics remain unchanged: default duration 60s includes 5s warmup, followed by TTL drain. Historical v2 artifacts remain immutable. Main mission behavior, renderers, FL/DQN/security contracts, authorization, radio and packet definitions remain intact.

## Verification outcomes

| Check | Outcome | Evidence |
|---|---|---|
| Configured Python suite coverage across runs | Passed | 661 final non-matrix tests + 310 simulator matrix cases; an additional retained-trace regression was executed afterward |
| Relevant post-repair Python/API checks | Passed | 61 serialization/study/API checks; 42 cancellation/mesh/API checks after terminal-publication repair |
| Frontend units | Passed | 173 tests; zero skipped |
| Type checking, lint, production build | Passed | No errors; existing lint and large-bundle warnings retained |
| Candidate/topology matrices | Passed | Ten candidates, sizes 10–100 by ten, seeds 7/42/99; structural, physical, switching, legacy, cooldown/failure tests |
| Production topology controls | Passed | 110 topology/fleet cases across eleven UI sizes including 75; formation/speed/scenario checks; twelve bounded workflows |
| Study lifecycle, uncertainty, exports and isolation | Passed | Actual authenticated API pilot, cancellation, all export formats, trace hash, unchanged live session; lost receipt generated one POST |
| Completed study presentation | Passed | **Transport fixture using the actual 550-run artifact**, not another backend study or fabricated measurements |
| Existing application/mission workflows | Passed | Navigation, fleet, policy/reporting, commands, attack groups/countdowns, stale states, route geometry, replay, WebGL fallback |
| Independent full-study verification | Passed | All 550 identities/accounts/input groups/artifact hashes, plus a 55-run stratified packet/cohort sample |
| Cross-process repeatability | Passed | Auto/normal, Ring/normal and Partial Mesh/regional jamming, pilot seed 7, fresh processes with Python hash seeds 7/99 |
| Unaffected fixed workloads | Passed | Eight pre-change baselines; packet/per-flow values exact; only recovery ordering and tiny mean-roundoff differences |
| Operator inputs and historical v2 results | Passed | Protected configuration/model/control hashes unchanged; v2 result files not overwritten |
| Original continuous matrix within 420 seconds | Failed | Timed out twice; replacement isolated matrix passes, but the old long-sequence deadline is not reclassified as passed |
| Remote Linux GitHub Actions execution | Not Tested | Configured and parsed; corresponding commands passed locally |
| RF hardware, real multi-hop SDN, calibrated energy | Not Tested | Deferred and excluded from this milestone |

Pytest's existing `tests/test_adversarial.py` script exclusion was not executed. Sustained rendering profiles were explicitly enabled outside routine PR CI. The completed-artifact rendering case is separately gated; CI smoke uses actual small API jobs with missing models and makes no learned-inference claim. This report does not claim every unrelated application/browser capability was exhaustively audited.

## Bugs, interruptions and repairs

1. **Ineffective congestion:** legacy Base-directed floods dropped before mesh transmission. The shared v3 peer driver now loads real radios and queues. A pilot Star run offered 8,500 background packets, started 4,904 hop transmissions, hit the 64-packet queue limit and recorded 5,923 queue-overflow drops. Historical results remain unchanged.
2. **Cross-process nondeterministic export:** recovery insertion order changed serialized lists despite identical packet traces/measurements. Recovery records and averaging order now sort by source/destination. The raw execution remains unchanged. Canonicalization changes 133 recovery-average fields by at most **1.09e-11 ms** from floating-point accumulation order. Individual packet/per-flow measurements, delivery comparisons and metric definitions are unchanged. Regression tests include non-associative positive timing sums.
3. **Terminal trace race:** fixed benchmarks could report cancellation before retained trace links were published, stopping frontend polling too early. Terminal publication now follows closed trace files and marks incomplete traces. Python and actual browser download/decompression regressions pass.
4. **Storage interruptions:** the first execution stopped at 290 completed runs; continuation stopped at 337 when a global largest-trace projection became overly conservative. Both Failed records and incomplete traces remain available. Offline continuation validates source/protocol/trace hashes and writes a new directory. It projects policy-specific maximum bytes per offer against remaining traffic, with 50% headroom and 512 MiB reserve; the worker low-space guard remains. Final data combines 290 + 47 + 213 fresh completed observations without duplicate replications.
5. **Oversized browser test:** the old single matrix exceeded its 420s aggregate budget after 58 cases under concurrent load and 90 in the idle rerun. Per-fleet workflows plus a separate formation/speed/scenario workflow retain the original effect assertions and pass all 110 cases. The old long-session performance failure remains documented.

## Performance

Fresh, separate 60-second production profiles on the recorded Apple M2/macOS/Chrome machine. Targets: average ≥30 FPS, fifth percentile ≥20 FPS. A/B has two synchronized canvases.

| View | Average FPS | Fifth percentile FPS | Clock advance / 60 wall seconds | Outcome |
|---|---:|---:|---:|---|
| 2d | 58.74 | 59.52 | 33.5s | Passed |
| 3d | 54.26 | 30.03 | 29.2s | Passed |
| compare | 59.90 / 59.90 | 59.88 / 59.88 | 22.7s | Passed |

**Rendering target success is not real-time simulation success.** At requested 1×, clock advancement was about 0.56×, 0.49× and 0.38×. At 100 UAVs, live model evidence could become stale; the interface retained and labeled it. CPU/clock lag remains a known limitation, rather than being hidden by animation or wall-time metric substitution.

Matched 100-UAV Auto/Manual CPU profiles (seeds 7/42/99, 10 simulation seconds each) cost approximately **1.36–1.40×** Manual CPU time, using the same initial graph. Twenty alternating whole-selector evaluations have identical diagnostics and a 3.6% lower mean cost; variability makes that small overall gain inconclusive (the exploratory interval includes zero). No robust overall speedup is claimed.

The targeted candidate-copy operation has a clear component improvement over forty paired repetitions: **1.647 → 0.134 ms** mean CPU, **298,336 → 40,000 bytes** peak allocation, with identical node values. Ten candidates, cadence, emergency checks and exact packet timestamps remain. [CPU profiles](../cpu-profiles.json) and [rendering records](../rendering-profiles.json) retain the detailed measurements.

## Source and artifact binding

- Actual numerical execution source: `2aa38e615d4e03e9933b8e8328da93e4b2eab8d3d99ff702b44ed80cdf62e136`.
- Recovery serialization/aggregation source: `01b2ba0c508d784f0fa5ff1dd189a0a08827281f5bed716ec9480cf4ff9ec826`.
- Final application source, including terminal-publication repair: `a804b8e7a1fa62f451fad3b1b0e237c45a37940caa061f888f47b5c8ad691759`.
- Canonical scientific result hash: `fcfdfb134ebdecd202efeb28c75b526e9ce9b98f57e557e5ce1475a95e1ed9e0`.

The 550 runs executed on the archived execution source. **They were not silently relabeled as runs of the later export repair.** A separate source-bound canonical dataset was derived afterward, preserving raw results, packet traces and underlying measurements. Configurations, seeds, terrain/source hashes, interruption records and post-processing source accompany it. The final changes concern post-run serialization/aggregation and terminal publication, not packet outcomes or selector policy.

- [Per-run measurements](../per-run-measurements.csv)
- [All paired summaries](../statistical-summaries.json)
- [Independent verification](../independent-verification.json)
- [Study/seed/source summary](../study-summary.json)
- [Commands, exit codes, timestamps and log hashes](../commands.json)
- [Input isolation](../isolation.json)
- Full JSON/report/manifests and 550 compressed traces remain in `results/adaptive_resilience/evidence-v1-release/`. Large traces are deliberately excluded from Git; selected measurements and summaries accompany this report.

## Remaining limitations and exact next steps

- **Clock lag:** reproduce with `FLARE_TEST_PRODUCTION=1 FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 FLARE_PROFILE_VIEW=3d FLARE_PROFILE_TOPOLOGY=auto npm --prefix frontend-react run test:browser -- mesh-performance.spec.ts`. Inspect clock progression as well as FPS. Further routing/selector/snapshot CPU work remains; do not infer real-time operation from FPS.
- **Continuous configuration deadline:** to reproduce safely, write `git show HEAD:frontend-react/e2e/control-matrix.spec.ts` to an audit-named temporary spec beside `workspace-helpers.ts`, then run only its original `frontend topology / size` test against the disposable production harness. Retain evidence and remove only that audit-owned temporary spec. The bounded replacement matrix is the supported regression gate; the old sequence did not finish within 420s.
- **Storage:** application estimates remain conservative and may stop early on limited space. Use the documented source-matched offline continuation; never overwrite an interrupted dataset or bypass the worker's low-space guard.
- **Remote CI/hardware:** push through the added workflow for Linux CI evidence. RF hardware, calibrated energy, full replay/history, cross-layer ablations, retraining and real multi-hop SDN remain later milestones.
- **Activation:** operator services and their running mission were not restarted/reset. New backend code loads on the next API/simulator restart.

See the [implementation/reproduction guide](../README.md). All scientific findings here are modeled packet simulation, not hardware validation.
