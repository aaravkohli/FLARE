"""
scripts/evaluate.py
Comprehensive evaluation of trained FL + RL models.

Loads:
  - models/fl_model.pth     (BiLSTM threat detector)
  - models/rl_model.zip     (deployed validation-best DQN path selector)
  - datasets/processed/test.csv  (held-out evaluation data)

Computes and saves:
  - results/evaluation_report.json
  - results/plots/
      confusion_matrix.png
      fl_training_curve.png  (if fl_round_metrics.csv exists)
      rl_reward_curve.png    (if rl tensorboard log exists)
      feature_importance.png

RL policies are compared on identical seeded ``DronePathEnv`` traces because
the tabular FL test CSV does not contain synchronized observations for all
three routes. A second robustness suite uses versioned synchronized three-path
CSV traces for in-distribution, persistent, barrage, and smart-jammer cases.
If a packet-derived ns-3 trace exists, a third suite evaluates the same policy
on validated UDP delivery, delay, and loss measurements.

Usage:
  python scripts/evaluate.py [--model-path models/fl_model.pth]
                             [--rl-model-path models/rl_model.zip]
                             [--test-csv datasets/processed/test.csv]
                             [--n-episodes 50] [--rl-eval-seed 42000]
"""

import argparse
import json
import logging
import os
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
_PLOT_CACHE = _RESULTS / ".plot_cache"
_PLOT_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_PLOT_CACHE / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(_PLOT_CACHE))

from fl.data import build_temporal_windows
from provenance import promote_run, write_experiment_run

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
    data_cfg = fl_cfg.get("data", {})
    seq_len = model_cfg["sequence_len"]

    from fl.checkpoint import validate_fl_checkpoint_metadata
    from fl.model import build_model

    if not model_path.exists():
        logger.error("FL model not found: %s", model_path)
        return {"error": f"Model not found: {model_path}"}

    try:
        validate_fl_checkpoint_metadata(
            model_path,
            model_config=model_cfg,
            data_config=data_cfg,
        )
    except Exception as exc:
        logger.error("FL checkpoint is incompatible: %s", exc)
        return {"error": str(exc)}

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

    windows = build_temporal_windows(
        pd.read_csv(test_csv),
        seq_len,
        stride=int(data_cfg.get("sequence_stride", 1)),
    )
    if not len(windows):
        return {"error": "No sequences could be built from test CSV"}

    X_t = torch.tensor(windows.features, dtype=torch.float32)
    y_j_list = windows.threat_labels[:, 0].astype(np.int8).tolist()
    y_a_list = windows.attack_labels.tolist()
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
    correctness = 0.5 * (
        (np.asarray(preds_j) == np.asarray(y_j_list)).astype(np.float32)
        + (np.asarray(preds_a) == np.asarray(y_a_list)).astype(np.float32)
    )
    confidence_brier = float(np.mean(
        (np.asarray(confs, dtype=np.float32) - correctness) ** 2
    ))

    logger.info("Threat detection  — F1: %.4f | Prec: %.4f | Rec: %.4f | Acc: %.4f",
                threat_f1, threat_prec, threat_rec, threat_acc)
    logger.info("Attack type acc   — %.4f", atk_acc)
    logger.info("Mean confidence   — %.4f", mean_conf)
    logger.info("Confidence Brier — %.4f (lower is better)", confidence_brier)

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
            "confidence_brier": confidence_brier,
            "classification_report": report,
        },
        "attack_classification": {
            "accuracy": atk_acc,
        },
        "num_test_sequences": len(windows),
        "device": str(device),
    }


# ---------------------------------------------------------------------------
# RL Model Evaluation
# ---------------------------------------------------------------------------

def evaluate_rl_model(
    rl_model_path: Path,
    test_csv: Path,
    n_episodes: int = 50,
    base_seed: int = 42_000,
) -> dict:
    """Compare the deployable DQN and baselines on identical seeded traces."""
    del test_csv  # FL held-out rows are not synchronized three-path RL traces.
    if not rl_model_path.exists():
        logger.warning("RL model not found: %s — skipping RL evaluation", rl_model_path)
        return {"skipped": True, "reason": f"Model not found: {rl_model_path}"}

    try:
        import yaml
        from rl.evaluation import evaluate_dqn_checkpoint

        rl_cfg = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
        env_cfg = rl_cfg["environment"]
        evaluation = evaluate_dqn_checkpoint(
            rl_model_path,
            observation_dim=env_cfg["obs_dim"],
            action_count=env_cfg["num_paths"],
            max_episode_steps=env_cfg["max_steps"],
            n_episodes=n_episodes,
            base_seed=base_seed,
            threat_threshold=float(
                rl_cfg.get("safety", {}).get("threat_threshold", 0.8)
            ),
        )
    except Exception as exc:
        logger.error("RL checkpoint is incompatible or evaluation failed: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    runtime = evaluation["policies"]["dqn_runtime"]
    greedy = evaluation["policies"]["greedy_lowest_threat"]
    logger.info(
        "RL runtime reward: %.4f ± %.4f (95%% CI); paired delta vs greedy: %.4f",
        runtime["mean_episode_reward"],
        runtime["reward_95ci_half_width"],
        runtime["paired_reward_delta_vs_greedy"]["mean"],
    )
    logger.info("Greedy reward: %.4f", greedy["mean_episode_reward"])
    return evaluation


def evaluate_rl_trace_suite(
    rl_model_path: Path,
    *,
    n_episodes: int = 50,
    generation_seed: int = 52_000,
) -> dict:
    """Generate and evaluate deterministic synchronized-path stress traces."""
    if not rl_model_path.exists():
        return {"skipped": True, "reason": f"Model not found: {rl_model_path}"}

    try:
        import yaml
        from rl.evaluation import evaluate_dqn_trace_suite
        from rl.traces import (
            ROBUSTNESS_SCENARIOS,
            generate_synchronized_trace,
            write_synchronized_trace,
        )

        rl_cfg = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
        env_cfg = rl_cfg["environment"]
        trace_dir = _BASE / "datasets" / "processed" / "rl_evaluation_traces"
        trace_paths = {}
        for scenario_index, scenario in enumerate(ROBUSTNESS_SCENARIOS):
            scenario_seed = generation_seed + scenario_index * 10_000
            frame = generate_synchronized_trace(
                scenario=scenario,
                n_episodes=n_episodes,
                steps_per_episode=int(env_cfg["max_steps"]),
                base_seed=scenario_seed,
            )
            trace_paths[scenario] = write_synchronized_trace(
                frame,
                trace_dir / f"{scenario}.csv",
            )

        result = evaluate_dqn_trace_suite(
            rl_model_path,
            trace_paths,
            observation_dim=int(env_cfg["obs_dim"]),
            action_count=int(env_cfg["num_paths"]),
            max_episode_steps=int(env_cfg["max_steps"]),
            n_episodes=n_episodes,
            # Explicit consecutive seeds enumerate every generated episode.
            base_seed=0,
            threat_threshold=float(
                rl_cfg.get("safety", {}).get("threat_threshold", 0.8)
            ),
        )
    except Exception as exc:
        logger.error("RL synchronized trace evaluation failed: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    for scenario, evaluation in result["scenarios"].items():
        runtime = evaluation["policies"]["dqn_runtime"]
        delta = runtime["paired_reward_delta_vs_greedy"]
        logger.info(
            "RL trace %-16s reward %.4f ± %.4f; delta vs greedy %.4f ± %.4f",
            scenario,
            runtime["mean_episode_reward"],
            runtime["reward_95ci_half_width"],
            delta["mean"],
            delta["95ci_half_width"],
        )
    return result


def evaluate_rl_ns3_packet_suite(
    rl_model_path: Path,
    packet_trace_path: Path,
    *,
    n_episodes: int = 50,
) -> dict:
    """Evaluate the DQN on every scenario in a packet-derived ns-3 trace."""
    if not rl_model_path.exists():
        return {"skipped": True, "reason": f"Model not found: {rl_model_path}"}
    if not packet_trace_path.exists():
        return {
            "skipped": True,
            "reason": f"Packet-derived trace not found: {packet_trace_path}",
        }

    try:
        import yaml
        from rl.evaluation import evaluate_dqn_trace_suite
        from rl.traces import load_synchronized_trace, trace_fingerprint

        frame = load_synchronized_trace(packet_trace_path)
        if not frame["source"].astype(str).str.startswith("ns3:packet-level:").all():
            raise ValueError("packet suite trace contains a non-ns-3 source")
        scenario_counts = (
            frame.groupby("scenario")["episode_id"].nunique().to_dict()
        )
        available_episodes = min(scenario_counts.values())
        evaluated_episodes = min(n_episodes, available_episodes)
        if evaluated_episodes < 2:
            raise ValueError(
                "packet suite requires at least two episodes per scenario, "
                f"found {scenario_counts}"
            )

        rl_cfg = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
        env_cfg = rl_cfg["environment"]
        result = evaluate_dqn_trace_suite(
            rl_model_path,
            {scenario: packet_trace_path for scenario in sorted(scenario_counts)},
            observation_dim=int(env_cfg["obs_dim"]),
            action_count=int(env_cfg["num_paths"]),
            max_episode_steps=int(env_cfg["max_steps"]),
            n_episodes=evaluated_episodes,
            base_seed=0,
            threat_threshold=float(
                rl_cfg.get("safety", {}).get("threat_threshold", 0.8)
            ),
        )
        result["packet_trace_protocol"] = {
            "trace_path": str(packet_trace_path.resolve()),
            "trace_sha256": trace_fingerprint(frame),
            "scenario_episode_counts": scenario_counts,
            "evaluated_episodes_per_scenario": evaluated_episodes,
            "threat_source": "qos-risk proxy, not FL inference",
        }
    except Exception as exc:
        logger.error("RL ns-3 packet-trace evaluation failed: %s", exc)
        return {"skipped": True, "reason": str(exc)}

    for scenario, evaluation in result["scenarios"].items():
        runtime = evaluation["policies"]["dqn_runtime"]
        delta = runtime["paired_reward_delta_vs_greedy"]
        logger.info(
            "RL ns-3 %-16s reward %.4f ± %.4f; delta vs greedy %.4f ± %.4f",
            scenario,
            runtime["mean_episode_reward"],
            runtime["reward_95ci_half_width"],
            delta["mean"],
            delta["95ci_half_width"],
        )
    return result


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
    import yaml

    rl_config = yaml.safe_load((_BASE / "config" / "rl_config.yaml").read_text())
    parser = argparse.ArgumentParser(description="Evaluate FL + RL models")
    parser.add_argument("--model-path",    type=Path, default=_BASE / "models/fl_model.pth")
    parser.add_argument("--rl-model-path", type=Path, default=_BASE / "models/rl_model.zip")
    parser.add_argument("--test-csv",      type=Path, default=_BASE / "datasets/processed/test.csv")
    parser.add_argument("--n-episodes",    type=int,  default=50)
    parser.add_argument("--rl-eval-seed",  type=int,  default=42_000)
    parser.add_argument("--rl-trace-seed", type=int,  default=52_000)
    parser.add_argument(
        "--ns3-trace",
        type=Path,
        default=_BASE / rl_config["paths"]["ns3_trace_csv"],
    )
    parser.add_argument(
        "--skip-rl-trace-suite",
        action="store_true",
        help="Skip synchronized in-distribution and stress trace evaluation",
    )
    parser.add_argument(
        "--skip-ns3-packet-suite",
        action="store_true",
        help="Skip evaluation of the generated packet-level ns-3 trace",
    )
    parser.add_argument(
        "--report", type=Path, default=_RESULTS / "evaluation_report.json"
    )
    parser.add_argument("--promote", action="store_true")
    args = parser.parse_args()

    if args.n_episodes < 2:
        parser.error("--n-episodes must be at least 2")

    rl_path = args.rl_model_path

    logger.info("═══ FL Model Evaluation ═══")
    fl_metrics = evaluate_fl_model(args.model_path, args.test_csv)

    logger.info("═══ RL Model Evaluation ═══")
    rl_metrics = evaluate_rl_model(
        rl_path,
        args.test_csv,
        n_episodes=args.n_episodes,
        base_seed=args.rl_eval_seed,
    )

    logger.info("═══ RL Synchronized Trace Robustness Suite ═══")
    rl_trace_metrics = (
        {"skipped": True, "reason": "disabled by --skip-rl-trace-suite"}
        if args.skip_rl_trace_suite
        else evaluate_rl_trace_suite(
            rl_path,
            n_episodes=args.n_episodes,
            generation_seed=args.rl_trace_seed,
        )
    )

    logger.info("═══ RL Packet-Level ns-3 Trace Suite ═══")
    rl_ns3_metrics = (
        {"skipped": True, "reason": "disabled by --skip-ns3-packet-suite"}
        if args.skip_ns3_packet_suite
        else evaluate_rl_ns3_packet_suite(
            rl_path,
            args.ns3_trace,
            n_episodes=args.n_episodes,
        )
    )

    logger.info("═══ Baseline Comparison ═══")
    baseline_metrics = evaluate_baselines(args.test_csv)

    # Plot FL training curve
    _plot_fl_training_curve()

    # Assemble report
    report = {
        "fl_model": fl_metrics,
        "rl_model": rl_metrics,
        "rl_trace_suite": rl_trace_metrics,
        "rl_ns3_packet_suite": rl_ns3_metrics,
        "baselines": baseline_metrics,
    }
    associated_plots = {}
    for name in ("confusion_matrix.png", "fl_training_curve.png"):
        if name == "confusion_matrix.png" and "threat_detection" not in fl_metrics:
            continue
        if name == "fl_training_curve.png" and not (_RESULTS / "fl_round_metrics.csv").is_file():
            continue
        plot_path = _PLOTS / name
        if plot_path.is_file():
            associated_plots[f"plots/{name}"] = plot_path.read_bytes()
    report["associated_artifacts"] = sorted(associated_plots)

    dataset_paths = [args.test_csv]
    if not args.skip_ns3_packet_suite:
        dataset_paths.append(args.ns3_trace)
    run_artifact = write_experiment_run(
        base=_BASE,
        experiment="model_evaluation",
        protocol_version="fl_rl_evaluation_v2",
        report=report,
        seeds=[args.rl_eval_seed, args.rl_trace_seed],
        evidence_category=(
            "packet_simulation"
            if not args.skip_ns3_packet_suite and args.ns3_trace.is_file()
            else "controlled_simulation"
        ),
        config_paths=[Path("config/fl_config.yaml"), Path("config/rl_config.yaml")],
        checkpoint_paths=[
            args.model_path,
            args.model_path.with_name(f"{args.model_path.name}.metadata.json"),
            args.rl_model_path,
            args.rl_model_path.with_name(f"{args.rl_model_path.name}.metadata.json"),
        ],
        dataset_paths=dataset_paths,
        dataset_roles={
            str(args.test_csv): "held_out_fl_evaluation",
            **(
                {str(args.ns3_trace): "packet_simulation_evaluation"}
                if not args.skip_ns3_packet_suite else {}
            ),
        },
        parameters={
            "episodes": args.n_episodes,
            "skip_rl_trace_suite": args.skip_rl_trace_suite,
            "skip_ns3_packet_suite": args.skip_ns3_packet_suite,
        },
        extra_files=associated_plots,
    )
    if args.promote:
        promote_run(
            base=_BASE,
            run_dir=run_artifact.run_dir,
            published_path=args.report,
            expected_experiment="model_evaluation",
            expected_protocol="fl_rl_evaluation_v2",
            required_seeds=[args.rl_eval_seed, args.rl_trace_seed],
        )
    logger.info("Immutable evaluation report saved → %s", run_artifact.report_path)

    # Print summary table
    print("\n" + "═" * 60)
    print("  EVALUATION SUMMARY")
    print("═" * 60)
    if "threat_detection" in fl_metrics:
        td = fl_metrics["threat_detection"]
        print(f"  FL Threat F1          : {td.get('f1_macro', 'N/A'):.4f}")
        print(f"  FL Threat Accuracy    : {td.get('accuracy', 'N/A'):.4f}")
        print(f"  FL Attack Type Acc    : {fl_metrics.get('attack_classification', {}).get('accuracy', 'N/A'):.4f}")
    if "policies" in rl_metrics:
        runtime = rl_metrics["policies"]["dqn_runtime"]
        greedy = rl_metrics["policies"]["greedy_lowest_threat"]
        delta = runtime["paired_reward_delta_vs_greedy"]
        print(
            "  RL Runtime Reward     : "
            f"{runtime['mean_episode_reward']:.4f} ± "
            f"{runtime['reward_95ci_half_width']:.4f} (95% CI)"
        )
        print(f"  Greedy Reward         : {greedy['mean_episode_reward']:.4f}")
        print(
            "  Paired Delta vs Greedy: "
            f"{delta['mean']:.4f} ± {delta['95ci_half_width']:.4f}"
        )
        print(f"  RL Action Dist        : {runtime['action_distribution_pct']}")
    if "scenarios" in rl_trace_metrics:
        print("  Synchronized trace suite:")
        for scenario, evaluation in rl_trace_metrics["scenarios"].items():
            runtime = evaluation["policies"]["dqn_runtime"]
            delta = runtime["paired_reward_delta_vs_greedy"]
            print(
                f"    {scenario:<16}: reward "
                f"{runtime['mean_episode_reward']:.4f} ± "
                f"{runtime['reward_95ci_half_width']:.4f}; "
                f"Δgreedy {delta['mean']:+.4f}"
            )
    if "scenarios" in rl_ns3_metrics:
        print("  Packet-level ns-3 suite:")
        for scenario, evaluation in rl_ns3_metrics["scenarios"].items():
            runtime = evaluation["policies"]["dqn_runtime"]
            delta = runtime["paired_reward_delta_vs_greedy"]
            print(
                f"    {scenario:<16}: reward "
                f"{runtime['mean_episode_reward']:.4f} ± "
                f"{runtime['reward_95ci_half_width']:.4f}; "
                f"Δgreedy {delta['mean']:+.4f}"
            )
    if baseline_metrics:
        print(f"  Baseline Random F1    : {baseline_metrics.get('random_f1', 'N/A'):.4f}")
        print(f"  Baseline RSSI F1      : {baseline_metrics.get('rssi_thresh_f1') or 'N/A'}")
    print("═" * 60)
    print(f"  Plots → {_PLOTS}")
    print(f"  Report → {report_path}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
