"""
baselines/fl_baseline.py — [OPTIONAL]
Baseline FL models for research comparison against the BiLSTM.

Trains and evaluates:
  1. Logistic Regression
  2. Random Forest (100 trees)

Usage:
  python baselines/fl_baseline.py
"""

import logging
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent))
from fl.client import load_local_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [FL-BASELINE] %(message)s")
logger = logging.getLogger(__name__)


def prepare_dataset(num_samples: int = 1000, seq_len: int = 10):
    """Flatten sequences into a 2D array for sklearn models."""
    X_n, y_n, _ = load_local_data("baseline_n", seq_len=seq_len, num_samples=num_samples // 2)
    X_j, y_j, _ = load_local_data("baseline_j", seq_len=seq_len, num_samples=num_samples // 2, jammed=True)

    X_all = np.concatenate([X_n.numpy(), X_j.numpy()], axis=0)
    y_all = np.concatenate([y_n.numpy(), y_j.numpy()], axis=0)

    X_flat = X_all.reshape(len(X_all), -1)           # [N, T*5]
    y_bin  = (y_all.max(axis=1) > 0.5).astype(int)   # binary threat

    return X_flat, y_bin


def evaluate_model(name, model, X_test, y_test):
    y_pred = model.predict(X_test)
    try:
        auc = roc_auc_score(y_test, model.predict_proba(X_test)[:, 1])
    except Exception:
        auc = float("nan")
    acc = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=["clean", "jammed"], zero_division=0)
    logger.info("\n%s  Accuracy=%.4f  AUC=%.4f\n%s", name, acc, auc, report)
    return {"model": name, "accuracy": acc, "auc": auc}


def run_baselines():
    logger.info("Preparing dataset (2000 samples)...")
    X, y = prepare_dataset(num_samples=2000)
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)

    sc = StandardScaler()
    X_tr_s, X_te_s = sc.fit_transform(X_tr), sc.transform(X_te)

    results = []

    logger.info("Training Logistic Regression...")
    lr = LogisticRegression(max_iter=500, C=1.0, random_state=42)
    lr.fit(X_tr_s, y_tr)
    results.append(evaluate_model("LogisticRegression", lr, X_te_s, y_te))

    logger.info("Training Random Forest (100 trees)...")
    rf = RandomForestClassifier(n_estimators=100, max_depth=10, n_jobs=-1, random_state=42)
    rf.fit(X_tr, y_tr)
    results.append(evaluate_model("RandomForest", rf, X_te, y_te))

    print("\n" + "=" * 50)
    print(f"{'Model':<25} {'Accuracy':>10} {'AUC':>10}")
    print("=" * 50)
    for r in results:
        print(f"{r['model']:<25} {r['accuracy']:>10.4f} {r['auc']:>10.4f}")
    print("=" * 50)
    return results


if __name__ == "__main__":
    run_baselines()
