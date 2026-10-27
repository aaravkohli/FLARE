# Second simulator performance improvement

**Passed: another measured CPU reduction with identical scientific results in the verified cases.** Against the previous optimized source, 100-UAV live workloads use 16.8–17.9% less CPU and reference workloads use 20.8–21.9% less. The workload, all ten topology candidates, evaluation cadence, radio model, packet timing and security policy are preserved.

This is a computational improvement. It does not turn the exploratory pilot into a positive resilience-quality result; its delivery and freshness measurements remain unchanged.

## Changes and correctness boundary

1. Compute a conservative upper height bound for the native immutable terrain. Bilinear terrain interpolation and its edge blend are convex combinations; the analytic extension cannot exceed 18 metres. Include floating-point margin derived from the elevation scale. If both endpoints clear this bound plus the existing three-metre clearance, the eight interior samples cannot find an obstruction and are skipped.
2. Continue all eight samples for low-altitude paths, unknown terrain providers, subclasses, and replaced instance/class samplers. Jamming, range, health, radio loss and bandwidth calculations still run normally. The bound is internal to the modeled terrain, not a physical RF guarantee.
3. Canonicalize a two-endpoint edge with one comparison instead of allocating and sorting a list. Preserve the original stable order for equal identities.
4. Look up each mesh edge once during route validation. Check current manual blocks, automatic blocks, availability, containment, threat and HOLD restrictions on every call. No permission cache was introduced.

Only `terrain.py`, `network.py`, `topology.py` and `engine.py` changed in production source during this pass. One regression module was added. No frontend, configuration, endpoint, dependency or model change was needed.

The follow-up profiler shows the remaining largest costs in topology candidate evaluation and packet scheduling. See [before](baseline-profile.txt) and [after](optimized-profile.txt) profiler records; profiler timings are diagnostic and are not substituted for the repeated CPU trials below.

## Matched CPU evidence

Three fresh-process repetitions for each workload and seed 7/42/99, five simulation seconds, 100 UAVs, snapshots every 0.2 simulation seconds. Execution order alternates between previous/new sources. Packet-event and snapshot hashing costs are included. The benchmark completed before the full regression and scenario jobs began.

| Workload | Seed | Previous CPU seconds | New CPU seconds | Reduction |
|---|---:|---:|---:|---:|
| live | 7 | 6.248 | 5.201 | 16.75% |
| live | 42 | 6.268 | 5.144 | 17.93% |
| live | 99 | 6.279 | 5.192 | 17.31% |
| reference | 7 | 4.460 | 3.533 | 20.78% |
| reference | 42 | 4.484 | 3.526 | 21.37% |
| reference | 99 | 4.514 | 3.527 | 21.87% |

All 18 matched pairs have identical exact packet-event payload/timestamp digests and authoritative snapshot digests. Session identity and wall/runtime/performance fields are excluded from snapshot comparisons; only session identity is excluded from packet-event comparisons. These runs do not use model inference. [All 36 trials](cpu-comparison.json), [CSV summary](cpu-comparison.csv).

Baseline source: `3b7c141c8d46c5296a666986d12bac5dd8830dad0e2af0286afd5c2c394991a4`.

Optimized source: `42aa7adc3d916db6019f6399a5f85665b7b5885923908861e1e18c3631b74564`.

## Verification

| Check | Outcome | Evidence |
|---|---|---|
| Independent radio sampling comparison | Passed | 9,000 high/low/threshold paths over seeds 7/42/99, including jamming, health and radio offsets |
| Bound and fallback correctness | Passed | Every 257×257 grid point, 10,000 extension/blend probes, strict threshold, overridden samplers and representative fallback |
| Actual sample avoidance | Passed | Runtime function-call profiling: zero samples on a proved-clear path; all eight on a low path |
| Edge and route authorization equivalence | Passed | Stable lexical/identity ordering plus 80,000 old/new authorization cases including quarantine, HOLD, threat, blocks, Direct, Mesh, Satellite and non-finite loss |
| Matched 100-UAV scientific outputs | Passed | 18 pairs / 36 fresh processes |
| Full-length scenario equivalence | Passed | 14 real 85-second reruns; 71,896 packet results, exact decompressed traces and all 58 scientific result fields match the accepted pilot |
| Full Python suite | Passed | 1,054 passed, five existing warnings, 381.36 seconds; `python-full.log` |
| Sustained live browser comparison | Passed | Fresh 100-UAV 3D sessions, same compiled frontend and checkpoint hashes |
| Requested live 1× speed | Failed | See actual clocks and achieved-speed diagnostics below |
| Frontend unit/type/lint suite after this pass | Not Tested | Frontend source unchanged; production browser uses byte-identical previously built assets |
| Excluded script-style adversarial suite | Not Tested | `pytest.ini` excludes `tests/test_adversarial.py`; not executed in this optimization |
| Linux CI / physical RF / model retraining / new evidence study | Not Tested | Outside this computational optimization; historical artifacts retained |

The full-length reruns cover all six pilot scenarios for control/combined seed 7, plus normal combined seeds 42/99. Independent reduction and exact trace hashes are in [scenario-equivalence.json](scenario-equivalence.json). Temporary new traces were checked and removed; accepted original traces remain intact. This is equivalence validation, not a replacement 72-run study or rerun of the historical 550-run evidence.

## Live browser measurement

| Source | Achieved simulation speed | 60-second clock progression | Average FPS | Fifth-percentile FPS |
|---|---:|---|---:|---:|
| before | 0.505× | T+1.3s → T+31.6s | 54.43 | 30.03 |
| after | 0.573× | T+1.9s → T+36.8s | 53.58 | 30.03 |

The achieved-speed diagnostic improves by **13.45%**. The clocks advance 30.3 and 34.9 simulation seconds respectively during the 60-second measurements. Both rendering targets pass. Requested 1× remains unmet; the result is a partial improvement, not a real-time guarantee.

Both runs use private services on isolated test ports and the same production assets, viewport and model weights. The operator mission is not restarted. CPU, achieved simulation speed and rendering FPS are distinct measurements. The repeated offline CPU measurements support the computational claim; one fresh 60-second browser run per source shows live behavior on the reference machine.

## Reproduction and artifacts

[Source record](source-manifest.json), [execution commands](commands.json), [artifact hashes](artifact-manifest.json). Raw logs, profiler output and source archives are retained in `results/evidence_resilience/performance-improvement-v2/`. The preceding [performance report](../performance-improvement/README.md) remains unchanged and describes the earlier optimization.

```sh
venv/bin/python -m scripts.profile_swarm_hotpaths --baseline /absolute/path/to/extracted/previous/source --directory results/evidence_resilience/new-second-comparison --repeats 3 --seconds 5
venv/bin/python -m scripts.verify_swarm_hotpaths --input results/evidence_resilience/pilot-accepted-verified --directory results/evidence_resilience/new-second-equivalence
venv/bin/python -m pytest -q
```

The baseline source archive is the previous optimization's exact source. The browser baseline restores the four changed files in a private runtime tree; both private trees reuse the same compiled frontend artifacts. Output directories must be fresh. Do not mutate operator configuration or checkpoints to reproduce these tests.
