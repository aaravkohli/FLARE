# Evidence-aware resilience controller

This is an opt-in modeled-packet research mechanism. **Auto (balanced)** remains the Live Mission default. Select **Auto (safeguarded)** to enable `evidence_resilience_v1`. Manual topology selection pins the graph while retaining the selected observation and scheduling policy. Reset retains that choice but clears mission-bound observations, transitions and leases.

The operator's running services were not restarted or reset during implementation. An older running simulator must be restarted through the normal project startup procedure before it can expose the new policy; the frontend disables the new option when the backend lacks policy metadata. All browser validation used private service copies and separate ports.

## Mechanism

| Layer | Implementation | Behavior |
|---|---|---|
| Candidate selection | `simulation/swarm/adaptive_topology.py` | Keeps all ten candidates, existing score weights, cadence, hysteresis, cooldowns and backoff. Rejects normal candidates that worsen currently feasible protected deadlines or report coverage. Emergency recovery discloses unmet service budgets. |
| Transition coordinator | `simulation/swarm/resilience_control.py` | Prepares forwarding privately, captures affected packet identities, installs replacement forwarding at a network boundary, then retains only those packets' previous forwarding for at most one simulation second. Validates over two rebuilds. |
| Hop authorization | `simulation/swarm/engine.py` | Rechecks physical availability, containment, traffic-class restrictions, excluded nodes/links and installed policy. Retained edges cannot authorize new traffic or a newly installed route. Rollback never rewinds completed hops or accounting. |
| Observation transport | `simulation/swarm/observability.py` | Freezes one report per eligible UAV each simulation second, serializes the actual payload, fragments it within configured bounds and uses the authoritative mesh scheduler. Publishes only complete receipts within the three-second freshness budget. |
| Scheduling | `simulation/swarm/network.py` | Registered transactions and reports can use a deterministic, non-preemptive, work-conserving 10% per-radio protected reservation. Category labels alone do not establish eligibility. Existing capacity, retry and TTL constraints remain. |
| Model integration | `orchestrator/loop.py`, `api/server.py` | Processes each session/epoch/report identity once. Uses simulation age instead of refreshing evidence on polling. Missing reports preserve fail-closed behavior; receipt, detector decision and installed route remain distinct. |
| Live Mission | `TopologySelection.tsx`, `WorkspaceEvidence.tsx` | Shows active policy/topology, transition phase, captured packets, modeled deadline estimates, realized outcomes, coverage and retained evidence. Manual override remains explicit. |

The transient target-plus-retained graph is not declared a completed topology. Snapshots carry the incumbent, requested target, overlap phase, packet bindings and temporary edges. Both existing renderers continue to consume the authoritative link and packet-event streams; their cameras and coordinate mapping are unchanged.

Queue/radio/retry calculations are **modeled feasibility estimates**, not deterministic delivery guarantees. Unknown future losses, mobility and policy changes can invalidate them. A physically disconnected or quarantined UAV does not become reachable because a deadline is important.

## Pilot design

`evidence_resilience_pilot_v1` is separate from the historical 550-run `adaptive_resilience_v1` study. The latter's artifacts were not rewritten or rerun.

The final pilot has 72 runs: 30 UAVs, seeds 7/42/99, six scenarios, and four arms. Each run has five seconds warmup, sixty seconds of measured primary traffic, and the configured TTL drain (twenty seconds in the recorded configuration).

| Arm | Packet-aware transition | Evidence candidate guard | Protected scheduling | Receipt-backed reports |
|---|---|---|---|---|
| Balanced control | Off | Off | Off | On |
| Packet transitions | On | Off | Off | On |
| Observability guard | Off | On | On | On |
| Combined safeguards | On | On | On | On |

The balanced control is an experimental control with delivered observation traffic. It is **not** a claim that unmodified Live Mission's balanced policy already transports its observations this way. Existing A/B, fixed benchmarks and the older adaptive study explicitly remain on their original balanced policy.

A reproducibility repair applies to both policies: deferred packets now resume in original offer order, rather than process-dependent set order. Future Auto runs that encounter deferred traffic can therefore differ from older source revisions. Historical artifacts retain their original source identity; they are not rewritten or asserted numerically identical under the repair. The recorded fixed-topology golden workloads remain identical.

All arms share fleet identities, initial positions, motion, radio configuration, primary/background offers, report capture schedules, scenario targets and non-motion command schedules. Report payloads contain endogenous measurements, so their actual serialized lengths can differ across arms. Conditional acknowledgments and fragments have stable logical channel keys; additional event counts cannot shift unrelated channel draws.

Scenarios are normal, mobility, congestion, regional jamming, single-node failure and partition/reconnection. Partition is an imposed link cut, not physical movement. Congestion is the versioned peer workload: five 100-packet/s flows of 4096 bytes during [8,25)s, separately tagged from the primary 512-byte/UAV/second peer cohort. Commands set the standard role at 6,11,...,61 seconds with ten-second TTL, without changing trajectories. Reports stop at 65 seconds and must arrive before their three-second deadline.

Paired summaries use 10,000 deterministic seed-level percentile-bootstrap resamples. Packets are not independent experimental replications. Delivery differences are percentage points. Null measurements and unrecovered flows remain explicit. Acknowledgment latency conditions on received acknowledgments; latency/hops condition on delivered packets. Evidence recovery requires an actual loss of eligible freshness during the condition window, then restoration to at least 99% eligible freshness after its end. Normal has no recovery anchor.

**Captured-packet drops are a transition-exposed loss measure, not a counterfactual causal attribution.** Channel failures and queue pressure can also drop captured packets. Consult traces and paired arm differences; no additional forked mission or physical safety guarantee is implied.

`deadline_misses` counts protected packet finalizations at or after their deadline; early terminal losses are counted separately in `protected_failures`. It is not a transaction-level SLA-success count. Command acknowledgment ratio and complete report receipts supply the corresponding transaction outcomes. Airtime is summed radio-occupancy seconds (both hop endpoints count), measured even when reservation is disabled. The protected scheduler also preserves bounded queue headroom for registered traffic; this can trade ordinary-traffic delivery for report service under congestion.

## Reproduction

From the repository root, with the existing Python 3.11 environment and locked frontend dependencies:

```sh
venv/bin/python -m scripts.run_resilience_pilot --directory results/evidence_resilience/new-pilot
venv/bin/python -m scripts.verify_resilience_pilot --input results/evidence_resilience/new-pilot --directory results/evidence_resilience/new-pilot-verified
venv/bin/python -m pytest -q
npm --prefix frontend-react test
npm --prefix frontend-react run typecheck
npm --prefix frontend-react run lint
npm --prefix frontend-react run build
FLARE_TEST_PRODUCTION=1 npm --prefix frontend-react run test:browser -- resilience-controller.spec.ts
venv/bin/python -m scripts.profile_resilience_cpu
venv/bin/python -m scripts.profile_resilience_scheduler
venv/bin/python -m scripts.check_resilience_repeatability --directory results/evidence_resilience/new-repeat-check
```

Use fresh output directories; the runner refuses to overwrite previous runs. Disk preflight retains a 512 MiB reserve and projects trace storage. Browser tests bind disposable localhost ports 15173/18000/18080 and use private configurations/models. Do not point the harness at the operator ports.

For an interrupted pilot only, `venv/bin/python -m scripts.resume_resilience_pilot <original-directory> <new-directory> --workers 2` validates the exact source, protocol, run identities and retained trace hashes, then executes missing runs in a new directory. Original evidence is preserved. Prior interruptions remain in the result manifest and report. This does not add pause/resume to the API's Run/Cancel workflow.

The initial runner also accepts `--workers 2` for independent offline runs. API jobs remain single-worker. Per-run wall/CPU timings are diagnostic and are not pooled as if different worker counts represented identical performance conditions.

Run each sustained rendering profile separately with `FLARE_TEST_PRODUCTION=1 FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 FLARE_PROFILE_VIEW=2d FLARE_PROFILE_TOPOLOGY=safeguarded`; change the view to `3d` and `compare`. The spec is `mesh-performance.spec.ts`. Set `FLARE_ARTIFACT_DIR` to a fresh directory. Completed-result presentation can be checked with `FLARE_RESILIENCE_RESULT_FIXTURE=<absolute completed state.json>` and `resilience-results.spec.ts`; this last test is explicitly a transport fixture using real recorded results, not a second backend execution.

## Research hypothesis and nearby work

The proposed distinguishing hypothesis is the **coupling** of packet-specific transition retention with admissibility based on timely delivery of the security evidence needed to control the resulting network, under shared-radio contention and current security restrictions. No patent novelty conclusion follows from this implementation or pilot.

- [Reitblatt et al., Abstractions for Network Update (SIGCOMM 2012)](https://www.cs.cornell.edu/~jnfoster/papers/consistent-updates-sigcomm12.pdf) describes consistent network updates at packet and flow levels. Retaining old forwarding during updates is therefore not presented as new by itself.
- [US10271239B2](https://patents.google.com/patent/US10271239B2/en) describes cost-based reoptimization of UAV formation communication topology. Adaptive UAV topology selection alone is not the proposed distinction.
- [WO2025085443A1](https://patents.google.com/patent/WO2025085443A1/en) concerns network-route optimization using digital-twin emulation. Simulation-based configuration comparison alone is not the proposed distinction.

These are nearby technical references, not an exhaustive prior-art search, claim chart, legal-status opinion or patentability determination. Full counterfactual mission forks, active diagnostic probing, relay movement, model retraining and physical multi-hop enforcement are outside this milestone.

See [verification-report.md](verification-report.md) for actual outcomes, performance limits, defects and retained negative findings. Large execution logs, source archives and compressed traces remain in `results/evidence_resilience/`; their hashes are recorded with the release evidence.
