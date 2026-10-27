# Simulator hot-path improvement

**Passed: a verified computational improvement, with unchanged simulation results.** The optimized source uses 14–15% less CPU for the measured 100-UAV live workload and 17–18% less for its reference workload. This improves execution cost; packet delivery, report freshness, topology choices and scientific measurements retain their prior values in the verified cases.

## Changes

- Decode the immutable terrain elevations once, avoiding repeated conversion and interpolation closures during every radio check. Preserve interpolation arithmetic and the committed heightmap. Skip representative-terrain evaluation only where the existing blend is exactly one.
- Serialize link/packet scalar fields directly. Continue copying packet routes, restrictions and completed-hop history so a consumer cannot mutate authoritative packets through a snapshot.
- Keep all ten candidates, the evaluation cadence, packet timestamps, queue rules and radio/security calculations. No frontend, configuration, endpoint or dependency change.

The terrain cache is bounded by the heightfield: 66,049 entries, approximately 2.11 MB per Terrain instance on this Python runtime. It does not grow with mission duration.

## Matched measurements

Each table value is the mean of three fresh-process trials with five simulation seconds, 100 UAVs and snapshots every 0.2 simulation seconds. Baseline and optimized execution order alternates. Seeds are 7/42/99. Live and mesh-only reference workloads run independently. JSON serialization and packet/snapshot digest work are included in these measurements. They were run without the later regression/browser jobs competing for CPU.

CPU comparison data, all 36 trials and source identities are in [JSON](cpu-comparison.json) and [CSV](cpu-comparison.csv). Baseline source is `85844bbaf415323189d0c2a5fa100934cfc7f5fa720c5496a84d586e40aab5da`; optimized source is `3b7c141c8d46c5296a666986d12bac5dd8830dad0e2af0286afd5c2c394991a4`.

| Workload | Seed | Baseline CPU seconds | Optimized CPU seconds | Reduction |
|---|---:|---:|---:|---:|
| live | 7 | 7.363 | 6.308 | 14.32% |
| live | 42 | 7.399 | 6.264 | 15.34% |
| live | 99 | 7.401 | 6.334 | 14.42% |
| reference | 7 | 5.473 | 4.470 | 18.32% |
| reference | 42 | 5.477 | 4.498 | 17.88% |
| reference | 99 | 5.453 | 4.511 | 17.28% |

Every matched pair has identical exact packet-event payload/timestamp digests and authoritative snapshot digests. Snapshot comparisons exclude session identity, timestamp, wall-capture, runtime and performance fields; packet payload comparisons exclude only the session identifier. See the profiling script for the precise normalization. Timing measurements are excluded from scientific result hashes.

## Numerical and behavior checks

| Check | Outcome | Evidence |
|---|---|---|
| Matched 100-UAV scientific outputs | Passed | 18 baseline/optimized pairs, three repeated trials for each workload/seed |
| Independent old/new terrain computation | Passed | More than 20,000 random coordinates plus grid/edge/blend boundary probes; exact IEEE-754 byte equality; fallback checked |
| Packet/link wire serialization and mutable-data isolation | Passed | Original dataclass serializer as oracle; completed/retried scheduler events and five packet classes |
| Full-length scenario replay | Passed | 14 real 85-second reruns; all six scenarios for control/combined seed 7, plus normal combined seeds 42/99 |
| Full trace and measurement equality | Passed | 71,896 packet results independently reduced; exact decompressed trace SHA-256 matches and 58 scientific result fields per run |
| Simulator/API regression collection | Passed | 327 tests, two existing deprecation warnings; `regressions.log` |
| New focused tests | Passed | Seven tests; `hotpath-tests.log` |
| Sustained live browser comparison | Passed | Fresh baseline/optimized services, production frontend, 100 UAVs, 3D, 60 seconds each |
| Requested 1× real-time throughput | Failed | See actual clock progression below; FPS is a separate measure |
| Full application Python/frontend collection after this optimization | Not Tested | The affected 327 Python tests and production browser profile were executed; previous milestone's full-suite results are retained separately |
| New 550-run evidence study / detector training / physical RF | Not Tested | Outside this computational optimization; prior study artifacts were preserved |

The fourteen full reruns use the accepted study's captured configurations and fleet, not the live registry. Temporary new traces were independently checked, hashed and removed; hashes and comparisons remain in [scenario-equivalence.json](scenario-equivalence.json), with the original traces retained at their accepted location. These reruns validate equivalence; they do not replace the accepted 72-run pilot or establish a new resilience-quality advantage.

## Browser result and limits

| Source | Achieved simulation speed | 60-second clock progression | Average FPS | Fifth-percentile FPS |
|---|---:|---|---:|---:|
| before | 0.469× | T+1.1s → T+29.0s | 54.68 | 30.03 |
| after | 0.499× | T+1.1s → T+31.1s | 54.60 | 30.03 |

The reported achieved-speed diagnostic improved by **6.56%**. The authoritative clocks advanced 27.9 and 30.0 simulation seconds respectively during the 60-second samples. Requested 1× remains unmet; this is a measured partial improvement.

The before/after browser runs are separate fresh observations on the same Apple M2, Chromium, 1440×1000 viewport and DPR 1. Both use the same model files and byte-identical production frontend assets, without restarting operator services. The optimized run occurred before the final shared-asset baseline run. CPU timing and rendering FPS must not be pooled as if they were the same quantity. The sustained browser comparison is one run per source; the repeated CPU trials provide the replication for the computational claim.

The initial baseline used a separate production build with identical frontend source but differing generated utility CSS. That strict comparability check was **Failed**; its profiles are retained in the raw evidence as `browser-initial-*.json` and are superseded. The final baseline copies the optimized build unchanged; all asset hashes match. This change affects only disposable test preparation.

## Reproduction and source record

The raw logs/profiles and pre-change archive are in `results/evidence_resilience/performance-improvement-v1/`. [Artifact manifest](artifact-manifest.json) records their hashes. [Source manifest](source-manifest.json) records the exact source used. Do not overwrite the historical pilot or study directories.

```sh
venv/bin/python -m scripts.profile_swarm_hotpaths --baseline /absolute/path/to/extracted/prechange/source --directory results/evidence_resilience/new-hotpath-comparison --repeats 3 --seconds 5
venv/bin/python -m scripts.verify_swarm_hotpaths --input results/evidence_resilience/pilot-accepted-verified --directory results/evidence_resilience/new-hotpath-verification
venv/bin/python -m pytest -q tests/test_swarm_hotpath_equivalence.py tests/test_swarm_simulation.py tests/test_swarm_controls.py tests/test_swarm_api.py tests/test_mesh_research.py tests/test_evidence_resilience.py tests/test_adaptive_topology.py tests/test_adaptive_study.py tests/test_resilience_study.py
FLARE_TEST_PRODUCTION=1 FLARE_MESH_PROFILE=1 FLARE_PROFILE_SECONDS=60 FLARE_PROFILE_VIEW=3d FLARE_PROFILE_TOPOLOGY=safeguarded FLARE_ARTIFACT_DIR=/private/tmp/flare-new-3d-profile npm --prefix frontend-react run test:browser -- mesh-performance.spec.ts
```

Use fresh output directories. The scripts refuse to overwrite old comparison/verification directories. For a matched browser baseline, create a private runtime copy and restore only `network.py` and `terrain.py` from the recorded pre-change archive, then run the same profile against that copy. The private copy owns its configuration and model files; the operator fleet must not be used as mutable test state.
