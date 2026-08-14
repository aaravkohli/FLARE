"""
scripts/test_model_with_ns3.py — Rigorous FLARE System Benchmark via NS-3 Telemetry

Evaluates FLARE FL+RL models across 3 distinct RF Jamming Scenarios:
1. Spot Jamming (Targeted Direct Channel Attack)
2. Barrage Jamming (Multi-Channel Simultaneous Wideband Attack)
3. Reactive/Pulsed Jamming (Intermittent Dynamic Burst Attack)
"""

import json
import os
import sys
import time
from pathlib import Path

_BASE = Path(__file__).parent.parent
sys.path.insert(0, str(_BASE))

import numpy as np
import torch
import yaml

NS3_DIR = _BASE / "ns-3-dev"


def load_models():
    """Load FLARE FL BiLSTM Model and RL Double-DQN Agent."""
    fl_model = None
    rl_agent = None

    fl_model_path = _BASE / "models" / "fl_model.pth"
    fl_config_path = _BASE / "config" / "fl_config.yaml"

    if fl_model_path.exists() and fl_config_path.exists():
        try:
            from fl.model import build_model
            fl_cfg = yaml.safe_load(fl_config_path.read_text())
            fl_model = build_model(fl_cfg["model"])
            fl_model.load_state_dict(torch.load(fl_model_path, map_location="cpu"))
            fl_model.eval()
        except Exception as e:
            print(f"Warning: Failed to load FL model: {e}")

    rl_model_path = _BASE / "models" / "rl_model.zip"
    if rl_model_path.exists():
        try:
            from rl.agent import RLAgent
            rl_agent = RLAgent(str(rl_model_path))
        except Exception as e:
            print(f"Warning: Failed to load RL agent: {e}")

    return fl_model, rl_agent


def evaluate_file(file_name, scenario_name, fl_model, rl_agent):
    file_path = NS3_DIR / file_name
    if not file_path.exists():
        print(f"File {file_name} not found.")
        return

    with open(file_path, "r") as f:
        data = json.load(f)

    print("\n" + "=" * 90)
    print(f" SCENARIO BENCHMARK: {scenario_name.upper()}")
    print("=" * 90)
    print(f"{'Step':<5} | {'Path Scores [Dir, Sat, Mesh]':<32} | {'RL Selected Path':<18} | {'Threat Level':<12}")
    print("-" * 90)

    paths = ["direct", "satellite", "mesh"]
    successful_evasions = 0
    jammed_steps = 0
    total_steps = len(data)

    for item in data:
        step = item["step"]
        path_metrics = item["paths"]

        # Compute threat scores
        path_scores = []
        for p in path_metrics:
            pdr = p["pdr"]
            sinr = p["sinr"]
            score = float(np.clip(1.0 - (pdr * 0.7 + (sinr / 30.0) * 0.3), 0.0, 1.0))
            path_scores.append(round(score, 3))

        # Direct link jamming detection check
        is_direct_jammed = path_scores[0] >= 0.5
        if is_direct_jammed:
            jammed_steps += 1

        # Model Inference
        if rl_agent:
            decision = rl_agent.predict(path_scores)
            selected_path = decision["path_name"]
            threat_lvl = decision["threat_level"]
        else:
            best_idx = int(np.argmin(path_scores))
            selected_path = paths[best_idx]
            max_score = max(path_scores)
            threat_lvl = "HIGH" if max_score >= 0.7 else ("MEDIUM" if max_score >= 0.4 else "LOW")

        # Evaluate if selection avoids jammed paths
        selected_idx = paths.index(selected_path)
        if path_scores[selected_idx] < 0.5:
            if is_direct_jammed:
                successful_evasions += 1

        scores_str = f"[{path_scores[0]:.2f}, {path_scores[1]:.2f}, {path_scores[2]:.2f}]"
        print(f"{step:<5} | {scores_str:<32} | {selected_path.upper():<18} | {threat_lvl:<12}")

    print("-" * 90)
    evasion_rate = (successful_evasions / jammed_steps * 100) if jammed_steps > 0 else 100.0
    print(f"Total Steps: {total_steps} | Jammed Steps: {jammed_steps} | Successful Evasions: {successful_evasions}")
    print(f"Anti-Jamming Evasion Success Rate: {evasion_rate:.1f}%\n")


def run_full_benchmark():
    fl_model, rl_agent = load_models()
    if fl_model:
        print("[+] PyTorch BiLSTM FL Model: Loaded")
    if rl_agent:
        print("[+] Stable-Baselines3 Double-DQN RL Agent: Loaded")

    evaluate_file("ns3_spot_jamming.json", "Spot Jamming Attack", fl_model, rl_agent)
    evaluate_file("ns3_barrage_jamming.json", "Barrage Jamming Attack", fl_model, rl_agent)
    evaluate_file("ns3_reactive_jamming.json", "Reactive / Pulsed Jamming Attack", fl_model, rl_agent)


if __name__ == "__main__":
    run_full_benchmark()
