# Dataset Guide

## Overview

FL preprocessing supports three data sources (in priority order):

| Priority | Source | Size | Features | Jammed Labels |
|----------|--------|------|----------|---------------|
| 1 | **RadioML 2018.01A** | ~2 GB | Modulation/SNR → derived RF KPIs | Selected analog/continuous-wave modulation classes |
| 2 | **DroneRF** | ~4 GB | IQ windows → derived RF KPIs | Filename/drone-presence label |
| 3 | **Synthetic fallback** | ~50 MB | Generated RF KPI traces | Persistent stochastic attack episodes |

`preprocess.py` automatically combines whichever sources are available and saves
the 5-feature normalized CSV format used by `fl/client.py`.
Rows retain source-group/order provenance so FL can build ordered sliding windows
and keep complete traces on only one side of the train/test boundary.

RL does **not** treat these single-path rows as simultaneous route observations.
`python -m rl.traces` creates a separate versioned format containing direct,
satellite, and mesh state in every timestep. A separate ns-3 workflow produces
packet-derived rows under the same strict contract; see
[Synchronized RL traces](#synchronized-rl-traces).

---

## Quick Start (Synthetic Only — no download needed)

```bash
python datasets/download.py --synthetic-only
python datasets/preprocess.py --no-radioml --no-dronerf
```

This generates `datasets/processed/drone_{1,2,3}_train.csv` + `test.csv` from
the project's synthetic distribution and is sufficient to run `train_all.sh`.
If the file predates temporal trace metadata, regenerate it explicitly before
preprocessing:

```bash
python datasets/download.py --synthetic-only --force-synthetic
```

---

## Manual Dataset Download

### RadioML 2018.01A (2 GB, recommended)

The automated download uses DeepSig's public S3. If the SSL cert has expired, download manually:

1. Visit **https://www.deepsig.ai/datasets** → "RadioML 2018.01A"
   - or: **https://opendata.deepsig.io** (direct link, may require Chrome)
2. Download `GOLD_XYZ_OSC.0001_1024.hdf5` (the raw HDF5, **not** the tar)
3. Place at: `datasets/raw/GOLD_XYZ_OSC.0001_1024.hdf5`
4. Run: `python datasets/preprocess.py --no-dronerf`

> [!TIP]
> Alternative: download from **Kaggle** (search "RadioML 2018.01A") or use the
> [GNU Radio companion](https://wiki.gnuradio.org) to generate your own IQ dataset.

---

### DroneRF (~4 GB, optional)

DroneRF requires a free IEEE DataPort account to download.

1. Create a free account at **https://ieee-dataport.org**
2. Go to **https://ieee-dataport.org/open-access/dronerf**
3. Download `DroneRF.zip` (listed under "Dataset Files")
4. Place at: `datasets/raw/DroneRF.zip`  
   *(preprocess.py will auto-extract it)*
5. Run: `python datasets/preprocess.py`

---

## Preprocessed Output Format

All output CSVs in `datasets/processed/` have this schema:

| Column | Type | Range (unnormalised) | Notes |
|--------|------|---------------------|-------|
| `rssi` | float | -120 to -20 dBm | Normalised [0,1] by preprocess.py |
| `pdr` | float | 0 to 1 | Packet delivery ratio |
| `sinr` | float | -10 to 30 dB | Normalised [0,1] |
| `latency` | float | 0 to 1000 ms | Normalised [0,1] |
| `packet_loss` | float | 0 to 1 | 1 - pdr + noise |
| `jammed` | int | 0 or 1 | Ground truth label |
| `attack_type` | str | none/barrage/sweep/spot/unknown | String label |
| `attack_class` | int | 0–4 | Integer encoding |
| `path` | str | direct/satellite/mesh | Communication path |
| `drone_id` | str | drone_1/2/3 | Client partition |
| `source` | str | radioml/dronerf/synthetic | Dataset provenance |
| `sequence_group` | str | source capture/trace ID | Train/test split unit |
| `sequence_index` | int | non-negative source order | Sort key inside a temporal group |

The label for each model window comes from its final row. Windows never cross a
`sequence_group`, drone, or path boundary. `dataset_stats.json` records the
number of train/test groups and the verified overlap count.

`sequence_index` is a source-order key, not necessarily a wall-clock timestamp.
For RadioML it is the original HDF5 row index within a derived modulation/SNR,
drone, and path group; this is an inferred sequence contract because the source
examples are not a synchronized link-metric time series. DroneRF uses CSV
line/window order, while the synthetic fallback uses explicit trace position.

## Synchronized RL traces

Generate the disjoint defaults used by `python rl/train.py --trace-data`:

```bash
python -m rl.traces --scenario iid --episodes 200 --steps 500 --seed 42 \
  --output datasets/processed/rl_trace_iid_train.csv
python -m rl.traces --scenario iid --episodes 50 --steps 500 --seed 42000 \
  --output datasets/processed/rl_trace_iid_eval.csv
```

Each `synchronized_three_path_v1` row includes schema/source/scenario/seed
provenance, `episode_id`, contiguous `step`, and threat/normalized latency/loss/
jammed values for all three paths. Loading fails on missing fields, out-of-range
values, duplicate or gapped steps, or mixed episode metadata. Training also
fails if train/evaluation episode IDs overlap or their fingerprints are equal.
Controlled studies can instead pass `--mixture` entries such as
`iid=100 persistent_spot=50 barrage=30 reactive=20`. Mixture validation records
the exact per-scenario episode counts in the checkpoint sidecar. The latest
study reserved `smart_jammer` as an unseen-profile stress test. Its proportions
are controlled synthetic assumptions rather than measurements of a real
deployment's attack frequency, and its candidate was not promoted.

The default generated robustness suite uses `iid`, `persistent_spot`, `barrage`,
and `smart_jammer`. The contract also recognizes `clean` and `reactive` for
packet experiments. Generated scenarios are fixtures, not measured RF data.

### Packet-derived ns-3 traces

After configuring/building the external `ns-3-dev/` tree, generate timestamp-
aligned UDP packet intervals with:

```bash
python scripts/generate_ns3_traces.py \
  --scenarios persistent_spot barrage reactive \
  --episodes-per-scenario 10 --steps 30 --seed 60000
```

`simulation/ns3/flare_packet_trace.cc` creates independent direct, satellite,
and mesh point-to-point links, sends 100 fixed-size UDP packets per one-second
interval on each path, and applies deterministic receive-error profiles. Raw
`flare_ns3_packet_trace_v1` JSON retains transmitted/received packet counts,
received bytes, mean delay, throughput, loss, and the configured jam flag.
`rl/ns3_trace.py` verifies that every derived value agrees with the counters
before converting it to `synchronized_three_path_v1`.

The required threat field is a documented QoS-risk proxy—70% measured packet
loss plus 30% normalized mean delay—because this simulation does not run the FL
RF classifier. Each converted episode's `source` contains the canonical raw
JSON SHA-256. This is actual packet simulation, but the topology is still an
independent point-to-point error model rather than wireless RF, shared-medium,
mobility, or hardware evidence.

---

## File Locations After Preprocessing

```
datasets/
├── raw/
│   ├── GOLD_XYZ_OSC.0001_1024.hdf5   ← RadioML (place here manually)
│   ├── DroneRF.zip                    ← DroneRF (place here manually)
│   └── synthetic_fallback.csv         ← auto-generated by download.py
└── processed/
    ├── drone_1_train.csv   ← FL client 1 partition (~13k rows)
    ├── drone_2_train.csv   ← FL client 2 partition (~13k rows)
    ├── drone_3_train.csv   ← FL client 3 partition (~13k rows)
    ├── test.csv            ← held-out evaluation set (~10k rows)
    ├── dataset_stats.json  ← feature stats plus temporal split audit
    ├── rl_trace_iid_train.csv ← 200 synchronized RL training episodes
    ├── rl_trace_iid_eval.csv  ← 50 disjoint RL validation episodes
    ├── rl_trace_ns3.csv       ← converted packet-derived evaluation episodes
    └── ns3_raw/               ← raw packet counters/delay/throughput JSON + helper binary
```

---

## Dataset Stats (after preprocessing)

`datasets/processed/dataset_stats.json` is auto-generated and contains:

```json
{
  "rssi":        {"min": -114.98, "max": -40.01, "mean": ..., "std": ...},
  "pdr":         {"min":   0.00,  "max":   1.00, ...},
  "sinr":        {"min": -10.00,  "max":  30.00, ...},
  "latency":     {"min":   0.00,  "max": 999.99, ...},
  "packet_loss": {"min":   0.00,  "max":   1.00, ...},
  "class_balance": {
    "jammed_fraction": 0.40,
    "attack_distribution": {"none": 30000, "barrage": 5000, ...}
  },
  "temporal_contract": {
    "sequence_length": 10,
    "sequence_stride": 1,
    "group_column": "sequence_group",
    "order_column": "sequence_index",
    "group_overlap": 0
  }
}
```

---

## Feature Derivation from RadioML

| Our Feature | RadioML Source | Method |
|-------------|---------------|--------|
| `sinr` | `Z` column (SNR dB) | Direct mapping |
| `rssi` | Derived from SNR | `-60 + SNR*0.8 + N(0,3)` dBm |
| `pdr` | Derived from SNR | `sigmoid((SNR-5)/5)` |
| `packet_loss` | Derived from PDR | `1 - PDR + U(0,0.05)` |
| `latency` | Path-augmented | Synthetic: `U(path_lo, path_hi) × jam_mult` |
| `jammed` | Modulation class | Classes `3, 10, 16, 17, 18, 21, 22` are treated as analog/continuous-wave threats |
| `attack_type` | Threat modulation class | Threat classes through 16→barrage, through 21→sweep, otherwise→spot |
