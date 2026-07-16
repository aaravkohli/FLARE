"""
datasets/preprocess.py
Converts RadioML 2018.01A + DroneRF into the 5-feature CSV format used by
fl/client.py and rl/env.py.

Dataset locations (actual paths on this machine):
  RadioML : /Users/aaravkohli/Capstone/RadioML 2018.01A /GOLD_XYZ_OSC.0001_1024.hdf5
  DroneRF : /Users/aaravkohli/Capstone/DroneRF/{drone subdir}/*.rar  (CSV inside)

DroneRF filename convention:
  {5-bit-label}{H|L}_{index}.csv
  Label bits (MSB→LSB): drone_present | ar | bebop | phantom | ?
    00000 = Background (no drone)   → jammed = 0
    10000 = Bebop drone             → jammed = 1
    10100–10111 = AR drone          → jammed = 1
    11000 = Phantom drone           → jammed = 1
  H = High SNR segment, L = Low SNR segment

RadioML Z shape: (N, 1) int64, values are SNR in dB (−20 to +30)
RadioML X shape: (N, 1024, 2) float32, IQ samples

Output:
  datasets/processed/drone_{1,2,3}_train.csv
  datasets/processed/test.csv
  datasets/processed/dataset_stats.json

Usage:
  python datasets/preprocess.py                     # use both sources
  python datasets/preprocess.py --no-dronerf        # RadioML only
  python datasets/preprocess.py --no-radioml        # DroneRF only
  python datasets/preprocess.py --dry-run           # stats only, no write
  python datasets/preprocess.py --max-radioml 200000  # cap RadioML samples
"""

import argparse
import io
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

try:
    import rarfile
    import shutil
    import os
    unrar_tool = shutil.which("unrar")
    if not unrar_tool:
        for path in ["/opt/homebrew/bin/unrar", "/usr/local/bin/unrar", "/usr/bin/unrar"]:
            if os.path.exists(path):
                unrar_tool = path
                break
    rarfile.UNRAR_TOOL = unrar_tool or "unrar"
    HAS_RARFILE = True
except ImportError:
    HAS_RARFILE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PREPROCESS] %(message)s")
logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE    = Path(__file__).parent.parent
_PROC    = _BASE / "datasets" / "processed"
_PROC.mkdir(parents=True, exist_ok=True)

RADIOML_HDF5  = _BASE / "RadioML 2018.01A " / "GOLD_XYZ_OSC.0001_1024.hdf5"
DRONERF_ROOT  = _BASE / "DroneRF"

# ── Constants ─────────────────────────────────────────────────────────────────
ATTACK_TYPES    = ["none", "barrage", "sweep", "spot", "unknown"]
FEATURE_COLS    = ["rssi", "pdr", "sinr", "latency", "packet_loss"]
JAM_SNR_THRESH  = 4.0          # RadioML: SNR ≤ 4 dB → jammed
DRONE_SUBDIRS   = ["AR drone", "Bepop drone", "Phantom drone", "Background RF activites"]

# Per-path synthetic latency ranges (ms) — augmented on every record
_PATH_LATENCY = {
    "direct":    (5,   80),
    "satellite": (30,  200),
    "mesh":      (50,  400),
}

# DroneRF label from 5-bit prefix in filename
# 00000 = background, anything else = drone present → jammed
def _dronerf_is_jammed(label_bits: str) -> bool:
    return label_bits.strip("0") != ""


# ── Source 1: RadioML 2018.01A ─────────────────────────────────────────────────

def load_radioml(max_samples: int = 300_000) -> Optional[pd.DataFrame]:
    """
    Load RadioML 2018.01A HDF5 and derive RF KPIs.

    HDF5 keys:
      X : (N, 1024, 2)  IQ samples  float32
      Y : (N, 24)       one-hot modulation class  int64
      Z : (N, 1)        SNR in dB (integer, −20..+30)  int64

    Mapping → 5-feature schema:
      sinr        ← Z.squeeze() — SNR is a direct proxy for SINR
      rssi        ← −60 + sinr*0.8 + N(0,3)   [dBm]
      pdr         ← sigmoid((sinr−5)/5)
      packet_loss ← 1 − pdr + U(0,0.05)
      latency     ← augmented per path (synthetic, realistic ranges)
    """
    try:
        import h5py
    except ImportError:
        logger.error("h5py not installed. Run: pip install h5py")
        return None

    if not RADIOML_HDF5.exists():
        logger.warning("RadioML HDF5 not found: %s", RADIOML_HDF5)
        return None

    logger.info("Loading RadioML from %s (%.1f GB)...", RADIOML_HDF5.name,
                RADIOML_HDF5.stat().st_size / 1e9)
    rng = np.random.default_rng(42)

    with h5py.File(RADIOML_HDF5, "r") as f:
        n_total = f["Z"].shape[0]
        logger.info("  Total RadioML samples: %d", n_total)

        # Sub-sample
        if n_total > max_samples:
            idx = np.sort(rng.choice(n_total, size=max_samples, replace=False))
        else:
            idx = np.arange(n_total)

        # Z has shape (N, 1) — squeeze to (N,)
        snr      = f["Z"][idx].squeeze().astype(np.float32)   # (N,)
        mod_onehot = f["Y"][idx]                               # (N, 24)
        # No need to load X (IQ) — we derive everything from SNR + modulation

    mod_class = mod_onehot.argmax(axis=1)   # (N,) int, 0–23
    n = len(snr)
    logger.info("  Loaded %d RadioML samples", n)

    # ── Feature derivation ──────────────────────────────────────────────────
    sinr = snr.copy()

    rssi = -60.0 + sinr * 0.8 + rng.normal(0, 3, size=n).astype(np.float32)
    rssi = np.clip(rssi, -120.0, -20.0).astype(np.float32)

    pdr_raw = 1.0 / (1.0 + np.exp(-(sinr - 5.0) / 5.0))
    pdr     = np.clip(pdr_raw + rng.normal(0, 0.05, n), 0.0, 1.0).astype(np.float32)

    packet_loss = np.clip(1.0 - pdr + rng.uniform(0, 0.05, n), 0.0, 1.0).astype(np.float32)

    # NEW: Jammed label based on physical waveform (Analog/Continuous Wave = Threat)
    # Indices mapped to analog/continuous waveforms (e.g., FM, AM-DSB, AM-SSB, OOK)
    jam_modulations = [3, 10, 16, 17, 18, 21, 22] 
    jammed = np.isin(mod_class, jam_modulations).astype(np.int8)

    # Attack type: Normal digital is "none", analog jammers mapped to threat types
    attack_type = np.where(
        jammed == 0, "none",
        np.where(mod_class <= 16, "barrage",
        np.where(mod_class <= 21, "sweep", "spot"))
    )

    # Path and drone assignment (cycle / random)
    path_cycle = np.array(["direct", "satellite", "mesh"])[np.arange(n) % 3]
    drone_ids  = np.array(["drone_1", "drone_2", "drone_3"])[rng.integers(0, 3, n)]

    # Latency augmentation
    latency = _augment_latency(path_cycle, packet_loss, rng, n)

    df = pd.DataFrame({
        "rssi":        np.round(rssi, 2),
        "pdr":         np.round(pdr, 4),
        "sinr":        np.round(sinr, 2),
        "latency":     np.round(latency, 2),
        "packet_loss": np.round(packet_loss, 4),
        "jammed":      jammed,
        "attack_type": attack_type,
        "path":        path_cycle,
        "drone_id":    drone_ids,
        "source":      "radioml",
    })
    logger.info("  RadioML → %d rows, jammed=%.1f%%", len(df), 100*df["jammed"].mean())
    return df


# ── Source 2: DroneRF ─────────────────────────────────────────────────────────

def _parse_dronerf_label(filename: str):
    """
    Parse jammed flag and attack type from DroneRF filename.
    Filename looks like:  10100H_0.csv  or  00000L1_3.csv
    Label = first 5 characters (binary).
    H = High SNR segment, L = Low SNR.
    """
    stem = Path(filename).stem              # e.g. "10100H_0"
    label_bits = stem[:5]                   # "10100"
    snr_char   = stem[5] if len(stem) > 5 else "H"   # H or L
    is_jammed  = _dronerf_is_jammed(label_bits)
    # Physically, threat/jamming degrades the channel SNR (high_snr is False when jammed)
    high_snr   = not is_jammed

    # Drone type from label bits
    if label_bits == "00000":
        attack = "none"
    elif label_bits.startswith("10"):
        # AR drone or Bebop
        attack = "barrage"   # drone-induced interference classified as barrage
    elif label_bits.startswith("11"):
        attack = "sweep"     # Phantom classified as sweep
    else:
        attack = "unknown"

    return is_jammed, high_snr, attack


def _iq_to_rf_metrics(iq_row: np.ndarray, rng: np.random.Generator,
                       is_jammed: bool, high_snr: bool) -> dict:
    """
    Derive RF KPIs from a row of raw IQ samples.

    DroneRF CSVs: each row = one burst of IQ values (even or odd count).
    We split into I and Q halves (trimming to even length if needed),
    compute signal power, estimate SNR, then map to the 5-feature schema.
    """
    # Trim to even length so we can split cleanly into I and Q
    n_vals = len(iq_row)
    if n_vals % 2 != 0:
        iq_row = iq_row[:-1]
        n_vals -= 1
    if n_vals < 4:
        return None  # signal: skip this row

    half = n_vals // 2
    I = iq_row[:half].astype(np.float64)
    Q = iq_row[half:].astype(np.float64)

    signal_power = np.mean(I**2 + Q**2)
    if signal_power < 1e-12:
        signal_power = 1e-12

    inst_power  = I**2 + Q**2
    noise_floor = np.percentile(inst_power, 10)
    if noise_floor < 1e-12:
        noise_floor = 1e-12

    snr_db = float(10.0 * np.log10(signal_power / noise_floor))

    if high_snr:
        snr_db = np.clip(snr_db + rng.uniform(5, 15), -10, 30)
    else:
        snr_db = np.clip(snr_db - rng.uniform(5, 15), -10, 30)

    rssi = float(np.clip(-60.0 + snr_db * 0.8 + rng.normal(0, 2), -120, -20))
    pdr  = float(np.clip(1.0 / (1.0 + np.exp(-(snr_db - 5.0) / 5.0))
                         + rng.normal(0, 0.04), 0.0, 1.0))
    packet_loss = float(np.clip(1.0 - pdr + rng.uniform(0, 0.05), 0.0, 1.0))

    return {
        "rssi":        round(rssi, 2),
        "pdr":         round(pdr, 4),
        "sinr":        round(float(snr_db), 2),
        "packet_loss": round(packet_loss, 4),
    }


def load_dronerf(
    max_samples: int = 150_000,
    max_files_per_archive: int = 5,    # cap rows read per CSV inside a RAR
    rows_per_file: int = 200,          # rows sampled per CSV (each row = one burst)
) -> Optional[pd.DataFrame]:
    """
    Load DroneRF from RAR archives.
    Iterates all subdirs, opens each RAR via rarfile, reads CSVs in-memory.
    """
    if not HAS_RARFILE:
        logger.error("rarfile not installed. Run: pip install rarfile")
        return None

    if not DRONERF_ROOT.exists():
        logger.warning("DroneRF directory not found: %s", DRONERF_ROOT)
        return None

    rng = np.random.default_rng(99)
    records = []
    paths_cycle = ["direct", "satellite", "mesh"]

    # Collect all RAR files across all subdirs
    rar_files = sorted(DRONERF_ROOT.rglob("*.rar"))
    logger.info("Found %d DroneRF RAR archives in %s", len(rar_files), DRONERF_ROOT)

    for rar_path in rar_files:
        if len(records) >= max_samples:
            break

        # Determine label from parent directory name
        parent_name = rar_path.parent.name.lower()
        dir_is_background = "background" in parent_name

        try:
            rf = rarfile.RarFile(str(rar_path))
            csv_files = [n for n in rf.namelist() if n.endswith(".csv")]
            # Sample a subset of CSVs per archive (balance classes by sampling 4x more background files)
            files_to_sample = max_files_per_archive
            if dir_is_background:
                files_to_sample = max_files_per_archive * 4

            if len(csv_files) > files_to_sample:
                csv_files = list(rng.choice(csv_files, size=files_to_sample, replace=False))

            for csv_name in csv_files:
                if len(records) >= max_samples:
                    break

                # Parse label from CSV filename (not archive name)
                csv_stem = Path(csv_name).name
                try:
                    is_jammed, high_snr, attack_type = _parse_dronerf_label(csv_stem)
                except Exception:
                    is_jammed = not dir_is_background
                    high_snr  = True
                    attack_type = "none" if dir_is_background else "barrage"

                # Read CSV in-memory
                try:
                    with rf.open(csv_name) as f:
                        chunk = f.read(rows_per_file * 120_000)   # ~120KB/row estimate
                    text_lines = chunk.decode("utf-8", errors="ignore").splitlines()[:rows_per_file]
                    if not text_lines:
                        continue
                except Exception as e:
                    logger.debug("  Skipping %s: %s", csv_name, e)
                    continue

                for line in text_lines:
                    # Parse numeric values, skipping separator/header tokens
                    raw_vals = line.strip().split(",")
                    vals = []
                    for v in raw_vals:
                        v = v.strip()
                        if not v:
                            continue
                        try:
                            vals.append(float(v))
                        except ValueError:
                            pass  # skip '-' separators or text headers
                    if len(vals) < 10:
                        continue
                    iq = np.array(vals, dtype=np.float32)
                    window_size = 20000
                    n_windows = len(iq) // window_size
                    n_windows = min(n_windows, 100)
                    for w in range(n_windows):
                        iq_win = iq[w * window_size : (w + 1) * window_size]
                        metrics = _iq_to_rf_metrics(iq_win, rng, is_jammed, high_snr)
                        if metrics is None:
                            continue

                        path_name = paths_cycle[len(records) % 3]
                        p_loss    = metrics["packet_loss"]
                        latency   = _augment_latency_single(path_name, p_loss, rng)

                        records.append({
                            **metrics,
                            "latency":     round(latency, 2),
                            "jammed":      int(is_jammed),
                            "attack_type": attack_type,
                            "path":        path_name,
                            "drone_id":    f"drone_{(len(records) % 3) + 1}",
                            "source":      "dronerf",
                        })

            rf.close()

        except Exception as e:
            logger.warning("  Failed to read %s: %s", rar_path.name, e)
            continue

        logger.info("  Processed %s → %d records so far", rar_path.name, len(records))

    if not records:
        logger.warning("No DroneRF records extracted.")
        return None

    df = pd.DataFrame(records)
    logger.info("DroneRF → %d rows, jammed=%.1f%%", len(df), 100*df["jammed"].mean())
    return df


# ── Latency augmentation helpers ──────────────────────────────────────────────

def _augment_latency(paths: np.ndarray, packet_loss: np.ndarray,
                     rng: np.random.Generator, n: int) -> np.ndarray:
    latency = np.zeros(n, dtype=np.float32)
    for path_name, (lo, hi) in _PATH_LATENCY.items():
        mask = paths == path_name
        if mask.sum() == 0:
            continue
        base = rng.uniform(lo, hi, size=mask.sum()).astype(np.float32)
        mult = 1.0 + rng.uniform(4.0, 8.0, size=mask.sum()).astype(np.float32) * packet_loss[mask]
        latency[mask] = np.clip(base * mult, 0, 1000)
    return latency


def _augment_latency_single(path_name: str, packet_loss: float,
                             rng: np.random.Generator) -> float:
    lo, hi = _PATH_LATENCY.get(path_name, (5, 200))
    base   = rng.uniform(lo, hi)
    mult   = 1.0 + rng.uniform(4.0, 8.0) * packet_loss
    return float(np.clip(base * mult, 0, 1000))


# ── Synthetic fallback ─────────────────────────────────────────────────────────

def load_synthetic_fallback() -> pd.DataFrame:
    path = _BASE / "datasets" / "raw" / "synthetic_fallback.csv"
    if not path.exists():
        raise FileNotFoundError(
            "Synthetic fallback not found. Run: python datasets/download.py --synthetic-only"
        )
    df = pd.read_csv(path)
    if "source" not in df.columns:
        df["source"] = "synthetic"
    logger.info("Synthetic fallback → %d rows, jammed=%.1f%%", len(df), 100*df["jammed"].mean())
    return df


# ── Normalisation ─────────────────────────────────────────────────────────────

# Hard physical limits for min-max normalisation
_NORM_MINS = {"rssi": -120.0, "pdr": 0.0, "sinr": -10.0, "latency": 0.0,   "packet_loss": 0.0}
_NORM_MAXS = {"rssi":  -20.0, "pdr": 1.0, "sinr":  30.0, "latency": 1000.0, "packet_loss": 1.0}

def normalise_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in FEATURE_COLS:
        lo = _NORM_MINS[col]
        hi = _NORM_MAXS[col]
        df[col] = (df[col] - lo) / (hi - lo + 1e-8)
        df[col] = df[col].clip(0.0, 1.0).astype(np.float32)
    return df


def compute_stats(df: pd.DataFrame) -> dict:
    """Compute stats on RAW (unnormalised) values for reference."""
    stats = {}
    for col in FEATURE_COLS:
        stats[col] = {
            "min":  float(df[col].min()),
            "max":  float(df[col].max()),
            "mean": float(df[col].mean()),
            "std":  float(df[col].std()),
        }
    stats["class_balance"] = {
        "jammed_fraction":        float(df["jammed"].mean()),
        "total_samples":          len(df),
        "attack_distribution":    df["attack_type"].value_counts().to_dict(),
        "source_distribution":    df["source"].value_counts().to_dict(),
    }
    return stats


# ── Main preprocessing pipeline ───────────────────────────────────────────────

def preprocess(
    use_radioml:  bool = False,
    use_dronerf:  bool = True,
    max_radioml:  int  = 300_000,
    max_dronerf:  int  = 150_000,
    dry_run:      bool = False,
) -> None:
    dfs = []

    if use_radioml:
        logger.info("═══ Loading RadioML 2018.01A ═══")
        df_rm = load_radioml(max_samples=max_radioml)
        if df_rm is not None:
            dfs.append(df_rm)
        else:
            logger.warning("RadioML load failed — skipping.")

    if use_dronerf:
        logger.info("═══ Loading DroneRF ═══")
        df_drf = load_dronerf(max_samples=max_dronerf)
        if df_drf is not None:
            dfs.append(df_drf)
        else:
            logger.warning("DroneRF load failed — skipping.")

    if not dfs:
        logger.warning("No real data loaded — falling back to synthetic.")
        dfs.append(load_synthetic_fallback())

    # ── Merge & clean ──────────────────────────────────────────────────────
    df_all = pd.concat(dfs, ignore_index=True)
    df_all = df_all.dropna(subset=FEATURE_COLS + ["jammed"]).reset_index(drop=True)
    df_all = df_all.sample(frac=1, random_state=42).reset_index(drop=True)

    sources = df_all["source"].unique().tolist() if "source" in df_all.columns else ["?"]
    logger.info(
        "Combined: %d rows | jammed=%.1f%% | sources=%s",
        len(df_all), 100*df_all["jammed"].mean(), sources,
    )

    # ── Compute stats on raw values ────────────────────────────────────────
    stats = compute_stats(df_all)

    if dry_run:
        logger.info("[DRY RUN] Stats:")
        print(json.dumps(stats, indent=2))
        return

    # Save stats (raw values for reference)
    stats_path = _PROC / "dataset_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2))
    logger.info("Stats saved → %s", stats_path)

    # ── Encode attack class ────────────────────────────────────────────────
    atk_map = {a: i for i, a in enumerate(ATTACK_TYPES)}
    df_all["attack_class"] = (
        df_all["attack_type"].map(atk_map).fillna(4).astype(int)
    )

    # ── Normalise features to [0, 1] ──────────────────────────────────────
    df_all = normalise_features(df_all)

    # ── Stratified train / test split (80 / 20) ───────────────────────────
    df_train, df_test = train_test_split(
        df_all, test_size=0.2, random_state=42, stratify=df_all["jammed"],
    )

    df_test.reset_index(drop=True).to_csv(_PROC / "test.csv", index=False)
    logger.info("Test set → %s  (%d rows)", _PROC / "test.csv", len(df_test))

    # ── Partition training set into 3 drone partitions ────────────────────
    for i, drone_id in enumerate(["drone_1", "drone_2", "drone_3"]):
        df_drone = df_train[df_train["drone_id"] == drone_id].copy()

        # Pad to ~equal size if this partition is short
        target = len(df_train) // 3
        if len(df_drone) < target:
            extra = (df_train[df_train["drone_id"] != drone_id]
                     .sample(n=min(target - len(df_drone), len(df_train)),
                             random_state=i + 10))
            df_drone = pd.concat([df_drone, extra], ignore_index=True)

        df_drone = df_drone.sample(frac=1, random_state=i*7).reset_index(drop=True)
        out = _PROC / f"drone_{i+1}_train.csv"
        df_drone.to_csv(out, index=False)
        logger.info(
            "Drone %d → %s  (%d rows, jammed=%.1f%%)",
            i+1, out.name, len(df_drone), 100*df_drone["jammed"].mean(),
        )

    logger.info("Preprocessing complete. All files in %s", _PROC)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Preprocess RadioML + DroneRF")
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--no-radioml",   action="store_true")
    parser.add_argument("--no-dronerf",   action="store_true")
    parser.add_argument("--max-radioml",  type=int, default=300_000,
                        help="Max RadioML samples to load (default 300k)")
    parser.add_argument("--max-dronerf",  type=int, default=150_000,
                        help="Max DroneRF records to extract (default 150k)")
    args = parser.parse_args()

    preprocess(
        use_radioml  = not args.no_radioml,
        use_dronerf  = not args.no_dronerf,
        max_radioml  = args.max_radioml,
        max_dronerf  = args.max_dronerf,
        dry_run      = args.dry_run,
    )


if __name__ == "__main__":
    main()
