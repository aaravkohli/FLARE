"""
scripts/evaluate.py
Comprehensive evaluation of trained FL + RL models.

Loads:
  - models/fl_model.pth     (BiLSTM threat detector)
  - models/best_model.zip   (DQN path selector) or models/rl_model.zip
  - datasets/processed/test.csv  (held-out evaluation data)

Computes and saves:
  - results/evaluation_report.json
  - results/plots/
      confusion_matrix.png
      fl_training_curve.png  (if fl_round_metrics.csv exists)
      rl_reward_curve.png    (if rl tensorboard log exists)
      feature_importance.png

Usage:
  python scripts/evaluate.py [--model-path models/fl_model.pth]
                             [--rl-model-path models/best_model.zip]
                             [--test-csv datasets/processed/test.csv]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EVAL] %(message)s")
logger = logging.getLogger(__name__)

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))

_RESULTS  = _BASE / "results"
_PLOTS    = _RESULTS / "plots"
_RESULTS.mkdir(parents=True, exist_ok=True)
_PLOTS.mkdir(parents=True, exist_ok=True)

FEATURE_COLS = ["rssi", "pdr", "sinr", "latency", "packet_loss"]
ATTACK_CLASSES = ["none", "barrage", "sweep", "spot", "unknown"]


# ---------------------------------------------------------------------------
# FL Model Evaluation
# ---------------------------------------------------------------------------

def evaluate_fl_model(model_path: Path, test_csv: Path) -> dict:
    """
    Evaluate the BiLSTM model on the held-out test set.
    Returns a dict of all metrics.
    """
    import torch
    from torch.utils.data import DataLoader, TensorDataset
    from sklearn.metrics import (
        f1_score, accuracy_score, classification_report,
        confusion_matrix, precision_score, recall_score,
    )
    import yaml

    fl_cfg = yaml.safe_load((_BASE / "config" / "fl_config.yaml").read_text())
    model_cfg = fl_cfg["model"]
    seq_len = model_cfg["sequence_len"]

    from fl.model import build_model, set_model_weights

    if not model_path.exists():
        logger.error("FL model not found: %s", model_path)
        return {"error": f"Model not found: {model_path}"}

    # Select device
    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")
    logger.info("FL evaluation device: %s", device)

    model = build_model(model_cfg).to(device)
    state = torch.load(model_path, map_location=device)
    model.load_state_dict(state)
    model.eval()

    if not test_csv.exists():
        logger.error("Test CSV not found: %s", test_csv)
        return {"error": f"Test CSV not found: {test_csv}"}

    df = pd.read_csv(test_csv)
    df = df.dropna(subset=FEATURE_COLS + ["jammed"]).reset_index(drop=True)
    feats   = df[FEATURE_COLS].values.astype(np.float32)
    jammed  = df["jammed"].values.astype(np.int8)
    atk_col = "attack_class" if "attack_class" in df.columns else None

    # Build sequences by tile-repeating each individual row
    X_list, y_j_list, y_a_list = [], [], []
    for i in range(len(feats)):
        window = np.tile(feats[i], (seq_len, 1))  # [seq_len, 5]
        X_list.append(window)
        y_j_list.append(int(jammed[i]))
        y_a_list.append(int(df[atk_col].values[i]) if atk_col else 0)

    if not X_list:
        return {"error": "No sequences could be built from test CSV"}

    X_t = torch.tensor(np.stack(X_list), dtype=torch.float32)
    loader = DataLoader(TensorDataset(X_t), batch_size=512)

    preds_j, preds_a, confs = [], [], []
    with torch.no_grad():
        for (xb,) in loader:
            xb = xb.to(device)
            out = model(xb)
            threat_score = out.path_scores.mean(dim=1).cpu().numpy()
            preds_j.extend((threat_score > 0.5).astype(int).tolist())
            preds_a.extend(out.attack_logits.argmax(dim=1).cpu().numpy().tolist())
            confs.extend(out.confidence.squeeze().cpu().numpy().tolist()
                         if out.confidence.dim() > 0 else [float(out.confidence.cpu())])

    # Metrics
    threat_f1  = float(f1_score(y_j_list, preds_j, average="macro", zero_division=0))
    threat_prec = float(precision_score(y_j_list, preds_j, average="macro", zero_division=0))
    threat_rec  = float(recall_score(y_j_list, preds_j, average="macro", zero_division=0))
    threat_acc  = float(accuracy_score(y_j_list, preds_j))
    atk_acc     = float(accuracy_score(y_a_list, preds_a))
    mean_conf   = float(np.mean(confs))

    logger.info("Threat detection  — F1: %.4f | Prec: %.4f | Rec: %.4f | Acc: %.4f",
                threat_f1, threat_prec, threat_rec, threat_acc)
    logger.info("Attack type acc   — %.4f", atk_acc)
    logger.info("Mean confidence   — %.4f", mean_conf)

    # Confusion matrix plot
    _plot_confusion_matrix(y_j_list, preds_j, labels=["normal", "jammed"])

    # Classification report
    report = classification_report(
        y_j_list, preds_j,
        target_names=["normal", "jammed"],
        output_dict=True,
        zero_division=0,
    )

    return {
        "threat_detection": {
            "f1_macro":   threat_f1,
            "precision":  threat_prec,
            "recall":     threat_rec,
            "accuracy":   threat_acc,
            "mean_confidence": mean_conf,
            "classification_report": report,
        },
        "attack_classification": {
            "accuracy": atk_acc,
        },
        "num_test_sequences": len(X_list),
        "device": str(device),
    }


# ---------------------------------------------------------------------------
# RL Model Evaluation
# ---------------------------------------------------------------------------

def evaluate_rl_model(rl_model_path: Path, test_csv: Path, n_episodes: int = 20) -> dict:
    """Evaluate DQN policy on RealDataDronePathEnv."""
    try:
        from stable_baselines3 import DQN
        from rl.env import RealDataDronePathEnv, DronePathEnv
    except ImportError as e:
        logger.error("RL dependencies missing: %s", e)
        return {"error": str(e)}

    if not rl_model_path.exists():
        logger.warning("RL model not found: %s — skipping RL evaluation", rl_model_path)
        return {"skipped": True, "reason": f"Model not found: {rl_model_path}"}

    model = DQN.load(str(rl_model_path))

    # Choose env: real data if CSV available, else synthetic
    if test_csv.exists():
        env = RealDataDronePathEnv(csv_path=test_csv)
        env_name = "RealDataDronePathEnv"
    else:
        env = DronePathEnv()
        env_name = "DronePathEnv (synthetic fallback)"
    logger.info("RL evaluation env: %s", env_name)

    episode_rewards, episode_lengths = [], []
    actions_taken = []

    for ep in range(n_episodes):
        obs, _ = env.reset()
        done = False
        ep_reward = 0.0
        ep_len = 0
        while not done:
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, terminated, truncated, info = env.step(int(action))
            ep_reward += reward
            ep_len += 1
            actions_taken.append(int(action))
            done = terminated or truncated
        episode_rewards.append(ep_reward)
        episode_lengths.append(ep_len)

    mean_reward = float(np.mean(episode_rewards))
    std_reward  = float(np.std(episode_rewards))
    logger.info("RL mean reward: %.4f ± %.4f", mean_reward, std_reward)

    action_counts = {f"path_{i}": actions_taken.count(i) for i in range(3)}
    path_names = ["direct", "satellite", "mesh"]
    action_pct = {path_names[i]: round(action_counts[f"path_{i}"] / len(actions_taken) * 100, 1)
                  for i in range(3)}
    logger.info("Action distribution: %s", action_pct)

    # RL reward curve from round metrics
    _plot_rl_reward_curve(episode_rewards)

    return {
        "mean_episode_reward":   mean_reward,
        "std_episode_reward":    std_reward,
        "min_episode_reward":    float(np.min(episode_rewards)),
        "max_episode_reward":    float(np.max(episode_rewards)),
        "mean_episode_length":   float(np.mean(episode_lengths)),
        "action_distribution_pct": action_pct,
        "n_episodes":            n_episodes,
        "env":                   env_name,
    }


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------

def evaluate_baselines(test_csv: Path) -> dict:
    """Compare BiLSTM against simple baselines: Random, Always-Jammed, Threshold-RSSI."""
    if not test_csv.exists():
        return {}

    from sklearn.metrics import f1_score
    df = pd.read_csv(test_csv).dropna(subset=["jammed"])
    y_true = df["jammed"].astype(int).values

    # Random baseline
    rng = np.random.default_rng(42)
    random_preds = rng.integers(0, 2, size=len(y_true))
    random_f1 = float(f1_score(y_true, random_preds, average="macro", zero_division=0))

    # Always-predict-0 baseline
    always_0_f1 = float(f1_score(y_true, np.zeros_like(y_true), average="macro", zero_division=0))

    # RSSI threshold baseline: rssi < 0.35 (normalised) → jammed
    if "rssi" in df.columns:
        rssi = df["rssi"].values
        # normalise if needed
        if rssi.max() > 1.0:
            rssi = (rssi - (-120)) / ((-20) - (-120) + 1e-8)
        rssi_preds = (rssi < 0.35).astype(int)
        rssi_f1 = float(f1_score(y_true, rssi_preds, average="macro", zero_division=0))
    else:
        rssi_f1 = None

    logger.info("Baselines — Random F1: %.4f | Always-0 F1: %.4f | RSSI-thresh F1: %s",
                random_f1, always_0_f1, f"{rssi_f1:.4f}" if rssi_f1 else "N/A")

    return {
        "random_f1":      random_f1,
        "always_0_f1":    always_0_f1,
        "rssi_thresh_f1": rssi_f1,
    }


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def _plot_confusion_matrix(y_true, y_pred, labels):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import confusion_matrix
        import seaborn as sns

        cm = confusion_matrix(y_true, y_pred)
        fig, ax = plt.subplots(figsize=(6, 5))
        sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                    xticklabels=labels, yticklabels=labels, ax=ax)
        ax.set_ylabel("True Label")
        ax.set_xlabel("Predicted Label")
        ax.set_title("Threat Detection Confusion Matrix")
        fig.tight_layout()
        path = _PLOTS / "confusion_matrix.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        logger.info("Saved → %s", path)
    except Exception as e:
        logger.warning("Could not plot confusion matrix: %s", e)


def _plot_fl_training_curve():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        metrics_csv = _RESULTS / "fl_round_metrics.csv"
        if not metrics_csv.exists():
            return
        df = pd.read_csv(metrics_csv)
        if df.empty:
            return

        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        if "threat_f1" in df.columns:
            axes[0].plot(df["round"], df["threat_f1"], "b-o", markersize=4, label="Threat F1")
            axes[0].set_xlabel("FL Round")
            axes[0].set_ylabel("F1 Score")
            axes[0].set_title("Threat Detection F1 per Round")
            axes[0].legend()
            axes[0].grid(True, alpha=0.3)

        if "attack_accuracy" in df.columns:
            axes[1].plot(df["round"], df["attack_accuracy"], "g-s", markersize=4, label="Attack Acc")
            axes[1].set_xlabel("FL Round")
            axes[1].set_ylabel("Accuracy")
            axes[1].set_title("Attack Classification Accuracy per Round")
            axes[1].legend()
            axes[1].grid(True, alpha=0.3)

        fig.tight_layout()
        path = _PLOTS / "fl_training_curve.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        logger.info("Saved → %s", path)
    except Exception as e:
        logger.warning("Could not plot FL training curve: %s", e)


def _plot_rl_reward_curve(episode_rewards):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(episode_rewards, "r-o", markersize=5, label="Episode Reward")
        ax.axhline(y=np.mean(episode_rewards), color="k", linestyle="--",
                   label=f"Mean = {np.mean(episode_rewards):.3f}")
        ax.set_xlabel("Episode")
        ax.set_ylabel("Total Reward")
        ax.set_title("DQN Evaluation Episode Rewards")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        path = _PLOTS / "rl_reward_curve.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        logger.info("Saved → %s", path)
    except Exception as e:
        logger.warning("Could not plot RL reward curve: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Evaluate FL + RL models")
    parser.add_argument("--model-path",    type=Path, default=_BASE / "models/fl_model.pth")
    parser.add_argument("--rl-model-path", type=Path, default=_BASE / "models/best_model.zip")
    parser.add_argument("--test-csv",      type=Path, default=_BASE / "datasets/processed/test.csv")
    parser.add_argument("--n-episodes",    type=int,  default=20)
    args = parser.parse_args()

    # Try best_model first, fall back to rl_model
    rl_path = args.rl_model_path
    if not rl_path.exists():
        rl_path = _BASE / "models/rl_model.zip"

    logger.info("═══ FL Model Evaluation ═══")
    fl_metrics = evaluate_fl_model(args.model_path, args.test_csv)

    logger.info("═══ RL Model Evaluation ═══")
    rl_metrics = evaluate_rl_model(rl_path, args.test_csv, n_episodes=args.n_episodes)

    logger.info("═══ Baseline Comparison ═══")
    baseline_metrics = evaluate_baselines(args.test_csv)

    # Plot FL training curve
    _plot_fl_training_curve()

    # Assemble report
    report = {
        "fl_model": fl_metrics,
        "rl_model": rl_metrics,
        "baselines": baseline_metrics,
    }

    report_path = _RESULTS / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    logger.info("Evaluation report saved → %s", report_path)

    # Print summary table
    print("\n" + "═" * 60)
    print("  EVALUATION SUMMARY")
    print("═" * 60)
    if "threat_detection" in fl_metrics:
        td = fl_metrics["threat_detection"]
        print(f"  FL Threat F1          : {td.get('f1_macro', 'N/A'):.4f}")
        print(f"  FL Threat Accuracy    : {td.get('accuracy', 'N/A'):.4f}")
        print(f"  FL Attack Type Acc    : {fl_metrics.get('attack_classification', {}).get('accuracy', 'N/A'):.4f}")
    if "mean_episode_reward" in rl_metrics:
        print(f"  RL Mean Reward        : {rl_metrics['mean_episode_reward']:.4f} ± {rl_metrics['std_episode_reward']:.4f}")
        print(f"  RL Action Dist        : {rl_metrics.get('action_distribution_pct', {})}")
    if baseline_metrics:
        print(f"  Baseline Random F1    : {baseline_metrics.get('random_f1', 'N/A'):.4f}")
        print(f"  Baseline RSSI F1      : {baseline_metrics.get('rssi_thresh_f1') or 'N/A'}")
    print("═" * 60)
    print(f"  Plots → {_PLOTS}")
    print(f"  Report → {report_path}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
