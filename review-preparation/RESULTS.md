# Results you can explain and defend

Evidence was captured locally on 21 October 2026. This guide distinguishes controlled tests, software packet simulation and learned-model evaluation. No result below establishes physical UAV, RF, GPS or real satellite performance. Full source/evidence hashes are in [the manifest](evidence/manifest.json).

## The four facts to memorize first

1. A **512-byte, five-hop peer probe delivered in 48.16 ms of simulation time**, with no retries, in the prepared ten-UAV Ring rehearsal. This is one packet outcome, not a mean or field latency.
2. In **seed 42** of the **30-UAV normal-scenario topology benchmark**, Full Mesh delivered **96.00%** with **9.84 ms** average latency. Partial Mesh delivered **99.88%** with **38.50 ms**. This shows a configuration-specific trade-off, not a universally best topology.
3. A separate **retained v2 routing evaluation** gave DQN with safety **232.32 mean episode utility**, lowest-threat routing **209.59**, static Direct **119.94**, and myopic utility **231.79**. Utility is dimensionless and defined by that experiment. It is not PDR, accuracy or a percentage.
4. The copied-model **FL Update Gate passed all five checks in the rehearsal**. It rejected its constructed adversarial update and left the deployed model unchanged. Its “damage reduction” measures aggregate distance to a clean control, not classifier accuracy.

A useful answer to “How accurate is the detector?” is: **“We have controlled synthetic detection checks, but these do not establish representative field accuracy for the deployed BiLSTM. I would not quote one overall accuracy for the whole project.”**

## 1. Actual live demonstration observations

The initial rehearsal is saved in `evidence/demo-rehearsal.json`, `demo-result.json` and `demo-probe-result.json`.

| Observation | Verified meaning | What it does not prove |
|---|---|---|
| Probe `p1` delivered across UAV-001 through UAV-006 | Five event-timed hops, 512 bytes, no retries, completion at T+0.04815748 s | A typical network-wide latency or hardware performance |
| Direct Spot jam accepted for 15 simulation seconds | Authoritative condition, paused timer, automatic expiry | A guaranteed detector label or policy transition |
| `source=fl_model`, `routing_source=rl_model`, v3 contract | Runtime reported learned detector/policy inference for that capture | Calibrated confidence or classification accuracy |
| Satellite installed before and during jam | The alternative remained installed | A Direct-to-Satellite reroute in this run |
| Direct risk rose from about 0.92 to near 1 | The model reported high Direct risk before and during injection | That injection caused every existing risk, or that an attack-class label became “Spot” |
| Network-security analysis reported NORMAL during the jam capture | Traffic misconduct analysis differed from RF risk | Failure to execute the jam, or proof of malicious forwarding |
| Active condition ended automatically | The injection expired without manual clear | Immediate erasure of rolling loss, detector history or containment |

The `attack_type` field in that model capture remained `none`. Do not equate injected profile, RF risk, traffic classification and enforcement. Show each separately.

## 2. Topology benchmark: one seed shown, all runs retained

**Protocol:** ten topologies, 30 UAVs, seeds 7/42/99, normal scenario, 350 m radio range, deterministic trust-aware mesh-only shortest-path routing. Satellite, ground-control transit and FL/DQN inference are excluded. The full recorded set contains 30 rows.

**Timing boundary:** configuration uses `warmup_s=5` and `duration_s=60`. The current implementation selects offered packets over **[5,60), a 55-second measured cohort**, then drains remaining TTL. The UI wording can suggest 60 seconds after warmup; that interpretation is not what this implementation measures. The result values below retain the existing calculations.

### Seed 42: exact cohort results

| Topology | Delivered / generated | Delivery | Mean delivered latency | Dropped |
|---|---:|---:|---:|---:|
| Full Mesh | 1584 / 1650 | 96.00% | 9.84 ms | 66 |
| Partial Mesh | 1648 / 1650 | 99.88% | 38.50 ms | 2 |
| Star | 1511 / 1650 | 91.58% | 31.91 ms | 139 |
| Ring | 1310 / 1650 | 79.39% | 141.82 ms | 340 |
| Tree | 1553 / 1650 | 94.12% | 40.97 ms | 97 |
| Cluster Mesh | 1569 / 1650 | 95.09% | 32.82 ms | 81 |
| Grid | 1648 / 1650 | 99.88% | 51.60 ms | 2 |
| Random Geometric | 1584 / 1650 | 96.00% | 9.84 ms | 66 |
| Hybrid Mesh | 1590 / 1650 | 96.36% | 29.35 ms | 60 |
| Self-healing Mesh | 1648 / 1650 | 99.88% | 39.30 ms | 2 |

**Say:** “Under these shared conditions, different graph structures trade delay against delivery. I compare the same seed and traffic protocol. The table alone does not establish why every drop occurred or prove that one topology wins under mobility or attacks.”

Full Mesh can have alternate graph paths. Do **not** explain its losses by claiming that it has no backup path. Inspect per-packet reasons before attributing a cause. Equal results for Full Mesh and Random Geometric here reflect this particular eligible graph/configuration, not universal equivalence.

Source: [all 30 runs](evidence/mesh-results.json). Repeated benchmark results retained the same result digest: `5f3c515883dc132929edce274307804757997ad178d9b3379c7c91bb63b0a98d`.

## 3. Retained v2 routing policy versus baselines

**Protocol:** `DronePathEnv`, `routing_qos_v2`, ten paired episodes of 500 steps, base seed 42000, safety threshold 0.8, least-risk fallback when all routes are unsafe. This evaluates the retained `models/rl_model.zip`, not the current v3 live contract.

| Policy | Mean episode utility | Reported 95% interval half-width | Mean switches | Mean jammed selections |
|---|---:|---:|---:|---:|
| DQN with runtime safety | 232.32 | 2.85 | 205.0 | 4.0 |
| Myopic utility baseline | 231.79 | 2.52 | 222.4 | 4.0 |
| Greedy lowest threat | 209.59 | 2.01 | 339.8 | 4.0 |
| Static Direct | 119.94 | 8.46 | 0.0 | 98.1 |
| Random | 10.46 | 7.37 | 340.7 | 99.0 |

DQN's paired mean utility difference versus greedy is **+22.73**, with reported 95% half-width **1.96**. Switches fell by about **39.7%** relative to greedy in these traces. That is a switch-count result, not a measured 39.7% reduction in RF handoff delay or real energy consumption.

**Explain utility:** “The test rewards useful communication and penalizes delay, loss, energy assumptions and switching. A larger score is better for this defined objective. It does not mean 232 percent reliability.”

**Explain the close myopic baseline:** “In this experiment the immediate-utility rule was close to DQN. This evidence does not establish a distinct long-horizon advantage or universal superiority.”

Static Direct selected jammed conditions more often, but this table does not justify saying measured throughput halved. Loss penalty components and utility are not actual delivered-packet throughput.

## 4. Current v3 safety evaluation

The current v3 checkpoint has four actions, including Network HOLD, and an 18-value observation contract. It differs from retained v2.

| Measurement | Observed result |
|---|---:|
| Evaluation episodes | 10 |
| Raw-policy unsafe-forward rate | 26.91% |
| Safety-filtered unsafe-forward rate | 0.00% |
| Clean-forward availability | 99.20% |
| HOLD precision | 98.94% |
| HOLD actions | 1892 |

**Say:** “In this controlled environment, the deterministic constraints removed unsafe forwards according to the test's own definition. The raw policy alone did not do that. We still need independent validation of measurements, assumptions and enforcement.”

Zero observed unsafe forwards is **not** a field safety guarantee or certification. The gate depends on its threat scores, containment/evidence inputs and executable controller behavior. Do not compare v3 reward 78.18 with v2 reward 232.32: their contracts/objectives differ.

Source for Sections 3–4: [routing evaluation](evidence/routing-evaluation.json).

## 5. Model-update poisoning study

**Protocol:** small synthetic **logistic-model** study, three rounds for each seed 7/42/99, a noisy client plus a 20× sign-flip poisoned client. This is **not** the deployed BiLSTM's threat-classification evaluation and is separate from the UI's copied-model self-test.

| Measurement | Result across three seeds |
|---|---:|
| Clean FedAvg mean classification accuracy | 95.73% |
| Attacked FedAvg mean classification accuracy | 8.80% |
| Protected mean classification accuracy | 95.93% |
| Malicious updates detected / submitted | 9 / 9 |
| Malicious updates rejected / submitted | 9 / 9 |
| Benign updates flagged / submitted | 4 / 36 |
| Benign updates rejected / submitted | 0 / 36 |
| Pooled detection precision | 69.23% |
| Pooled detection recall | 100.00% |

**Say:** “The attack badly damaged ordinary averaging in this constructed study. Trust-aware protection retained classification performance near the clean control. All nine malicious updates were rejected, but four benign updates were also flagged. None of those benign updates was rejected. That distinction matters.”

The 6.2% and 95.6% values in an individual seed are not the three-seed means. Do not combine one seed's accuracy with pooled counts and present it as one run. This small study does not establish resistance to colluding clients, subtle backdoors or all non-IID data.

Source: [Byzantine aggregation study](evidence/byzantine_aggregation.json). Why trust weighting: it gives less influence to suspicious or repeatedly inconsistent updates instead of treating every submitted vector as equally credible. Legitimate heterogeneity can also look unusual, which is why false positives and rejection are reported separately.

## 6. Controlled traffic detectors

| Study | Scope | Observed counts and metrics | Scientific boundary |
|---|---|---|---|
| Network security v2 | Synthetic counter distributions, six profiles, seeds 7/42/99, 180 samples | TP 120, TN 60, FP 0, FN 0; precision/recall 100% | Scripted counter test, not BiLSTM/RF/GPS/field accuracy |
| Insider detection v1 | Synthetic behavior records, five profiles, three rounds per seed | TP 34, TN 9, FP 0, FN 2; precision 100%, recall 94.44%, F1 97.14% | Binary abnormal-behavior detection, not reliable exact attack-class identification |

In the insider study, mean normal risk was 0.0315 and mean attack risk 0.6018. These are study averages, not thresholds that every attack must exceed. Some records mislabeled the attack class or remained false negatives. Zero false positives in nine benign records is limited evidence, not proof that false alarms cannot occur. No records reached quarantine in that study.

Sources: [network security](evidence/network_security.json), [insider detection](evidence/insider_detection.json).

## 7. Datasets, training and what remains unproven

- The preprocessing pipeline supports RadioML 2018.01A, DroneRF and synthetic traces. It derives/proxies communication KPIs and labels from the public RF sources. Those sources are not synchronized measured Direct/Satellite/Mesh histories containing every target feature and verified jam label.
- `sequence_group` boundaries keep windows from crossing train/test groups, UAVs or paths. RadioML source order is an inferred sequence, not real chronological radio-link telemetry.
- The deployed detector metadata reports sequence length 10, five features, three path-risk outputs and five attack classes. Twelve visible communication profiles do not mean twelve independently validated learned classes.
- The worktree has checkpoint metadata but no freshly verified representative held-out detector accuracy suitable for a field claim. Processed evaluation CSVs were absent in this checkout during the smoke runs.
- Bounded Flower/client-manager runs performed real optimizer updates and saved candidates under normal, jammed and poisoned conditions. They used five clients, one local epoch and up to roughly 500 synthetic windows per client. These were smoke tests, not convergence studies.
- A short FL resilience matrix exists in [the evidence](evidence/fl_resilience_matrix.json). One training round cannot establish convergence or deployment quality. Latest F1 values in a smoke with no representative evaluation set must not become “detector accuracy.”
- Advanced asynchronous buffering and adaptive client-selection settings still have live-integration gaps. Compression can retain a dense Flower wire payload. Differential privacy requires a stated accountant/configuration and assumptions; federated learning alone does not guarantee privacy.

The [bounded learning execution records](evidence/learning-smokes.json) retain both failures and later runs. Normal and jammed Flower cases passed after repairs; a poisoned mesh case failed to observe a protective action when updates did not provide the required delivered evidence. Satellite-transport poisoned cases passed. Seed 7's first jammed attempt completed only one round in 300 seconds; a fresh retry completed three. This is a reliability limitation, not an all-green training claim. DQN and the custom discrete SAC each completed 1,000-step optimizer/checkpoint/evaluation smoke runs for seeds 7/42/99. Those short runs verify executable learning paths, not comparative convergence or deployment quality.

## 8. Functional and rendering evidence

These are fresh audit executions saved on 21 October, not a claim that the entire application passed.

| Check | Recorded result | Interpretation |
|---|---|---|
| Python suite | 888 passed, 5 warnings | Covered assertions passed in that run |
| Frontend unit tests | 168 passed | Unit behavior, not the full browser application |
| Type check / production build | Passed | Compiles/builds |
| Lint | No errors, 7 existing warnings | Warnings remain |
| Latest complete production browser suite | 34 passed, 2 failed, 1 skipped | Unresolved failures in a topology/control matrix and a probe-enabled assertion after restored attacks |
| Earlier browser suite | 36 passed, 1 skipped | Earlier source/environment evidence, not a substitute for the latest failures |
| Targeted panel rehearsal | Passed core route | Exact main demo against real local services, with no fabricated detector label |
| Docker / Ryu / Mininet enforcement gate | Blocked | Docker daemon failed bounded preflight; no real forwarding claim |
| ns-3 wireless gate | Blocked | Required source/build runtime absent |

### 100-UAV rendering, separate 60-second runs

Reference machine: Apple M2, macOS, Chrome 155, headless production browser, 1440×1000 viewport, DPR 1. 3D used balanced quality with site details. This is a recorded local profile, not a portable guarantee.

| View | Average FPS | Fifth-percentile FPS | Simulation-clock change over 60 wall seconds |
|---|---:|---:|---|
| 2D | 58.47 | 59.52 | T+0 to T+19 s |
| 3D | 56.32 | 30.03 | T+0 to T+21 s |
| A/B panes | 60.00 | 59.88 | Both T+1 to T+29 s |

The average uses frame count divided by wall duration, while the percentile uses sampled frame intervals, so a percentile can exceed that average. The runs met the stated local rendering targets, but the simulated clock lagged wall time at dense load. Never call this “locked 60 FPS” or real-time 100-UAV execution.

## 9. Reading the interface's metrics

- **Delivery:** completed deliveries relative to the applicable offered/cohort definition. Check live versus experiment scope.
- **Latency:** packet timing for delivered packets. It does not include lost packets as very large made-up delays.
- **Jitter:** variation under the simulator's defined delivery statistic, not field radio jitter.
- **Throughput:** delivered modeled payload over the measurement interval, in Mb/s.
- **Hops:** completed route length for the relevant packet/flow cohort, not a DQN action ID.
- **Components:** disconnected graph groups under the current graph definition. It does not alone prove every intended flow delivered.
- **Route convergence:** time until a viable route returns after a recorded disruption.
- **Service recovery:** time until successful delivery returns. An installed route alone is not service recovery.
- **Unrecovered:** the observation never recorded recovery. Missing recovery stays unavailable, not zero.
- **Detector score versus RF loss:** estimated risk and observed/modeled packet loss are different quantities and may disagree.
- **Attribution:** signed feature contributions to the selected action's Q-value, not causal proof or necessarily Q-values for every family.

## 10. Claims to avoid

Avoid “all tests pass,” “100% detector accuracy,” “guaranteed safe,” “the jam always triggers rerouting,” “expiry resets threat to zero,” “full mesh cannot reroute,” “the scenery changes RF propagation,” “real drone testing,” “wire compression necessarily saved these bytes,” and “we are the first to combine these algorithms.” The evidence supports narrower, concrete explanations above.
