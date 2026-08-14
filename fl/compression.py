"""
fl/compression.py — Model Compression + Communication-Efficient FL  [FLARE v2]

Implements three compression strategies:
  1. Top-K Sparsification with Error Feedback
  2. INT8 Quantization (symmetric linear quantization)
  3. Combined (sparsify then quantize the non-zero values)

Error Feedback (Memory / Error Correction):
  Prevents the bias introduced by lossy compression from accumulating.
  Each client maintains a residual buffer e_t and adds it before compressing.

References:
  Alistarh, D. et al. (2018). QSGD: Communication-Efficient SGD via Gradient
  Quantization and Encoding. NeurIPS 2018. https://arxiv.org/abs/1610.02132

  Stich, S. et al. (2018). Sparsified SGD with Memory.
  NeurIPS 2018. https://arxiv.org/abs/1809.07599

  Lin, Y. et al. (2018). Deep Gradient Compression: Reducing the Communication
  Bandwidth for Distributed Training. ICLR 2018. https://arxiv.org/abs/1712.01887

Computational overhead:
  Top-K:       O(d log d) for partial sort — typically <0.5% wall time
  Quantize:    O(d) — ~4× smaller payload, near-zero overhead
  Combined:    O(d log d) — negligible vs network transfer savings

Communication reduction:
  Top-K (ratio=0.10): ~10× fewer values transmitted
  INT8 quantize:      ~4× size reduction (float32 → int8)
  Combined:           ~40× theoretical reduction

Unit-test hooks: run `python -m fl.compression` for self-test.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
Weights = List[np.ndarray]


# ---------------------------------------------------------------------------
# Error Feedback Buffer
# ---------------------------------------------------------------------------

class ErrorFeedbackBuffer:
    """
    Per-client residual accumulation buffer for error feedback compression.

    Error Feedback Algorithm (Stich et al. 2018):
        e_0 = 0
        g̃_t = compress(g_t + e_{t-1})
        e_t = g_t + e_{t-1} - decompress(g̃_t)

    This ensures lim_{t→∞} Σ e_t / T → 0, preventing bias accumulation.
    """

    def __init__(self):
        self._residuals: List[Optional[np.ndarray]] = []
        self._initialized = False

    def initialize(self, weights: Weights) -> None:
        """Initialize zero residuals matching the shape of weights."""
        self._residuals = [np.zeros_like(w) for w in weights]
        self._initialized = True

    def get_residuals(self) -> List[Optional[np.ndarray]]:
        return self._residuals

    def update(self, original: Weights, reconstructed: Weights) -> None:
        """Update residuals: e_t = (g_t + e_{t-1}) - decompress(compress(g_t + e_{t-1}))."""
        if not self._initialized:
            self.initialize(original)
            return
        self._residuals = [
            (orig + res) - recon
            for orig, res, recon in zip(original, self._residuals, reconstructed)
        ]
        total_residual_norm = sum(np.linalg.norm(r) for r in self._residuals)
        logger.debug("Error feedback residual L2 norm: %.6f", total_residual_norm)

    def add_to_weights(self, weights: Weights) -> Weights:
        """Return weights + accumulated residuals (g_t + e_{t-1})."""
        if not self._initialized:
            self.initialize(weights)
        return [w + r for w, r in zip(weights, self._residuals)]


# ---------------------------------------------------------------------------
# Top-K Sparsification
# ---------------------------------------------------------------------------

def topk_sparsify(
    weights: Weights,
    ratio: float = 0.10,
) -> Tuple[Weights, List[np.ndarray]]:
    """
    Retain only the top-K fraction of weight elements by absolute magnitude.
    Zero out all others. Returns both sparse weights and the binary mask.

    Algorithm (Lin et al. 2018 — Deep Gradient Compression):
        mask_i = |Δw_i| ≥ quantile(|Δw|, 1 - ratio)
        w_sparse_i = Δw_i * mask_i

    Args:
        weights: List of numpy arrays (weight deltas from global model).
        ratio:   Fraction of values to keep (e.g., 0.10 = keep top 10%).

    Returns:
        (sparse_weights, masks): sparse arrays and binary masks for reconstruction.

    Overhead: O(d log d) per layer for argsort/partition.
    """
    sparse_weights = []
    masks = []
    total_kept = 0
    total_params = 0

    for layer in weights:
        flat = layer.flatten()
        n = len(flat)
        k = max(1, int(n * ratio))

        # Partial sort: find the k-th largest magnitude threshold
        threshold_idx = np.argpartition(np.abs(flat), -k)[-k:]
        mask = np.zeros(n, dtype=np.float32)
        mask[threshold_idx] = 1.0

        sparse_flat = flat * mask
        sparse_weights.append(sparse_flat.reshape(layer.shape))
        masks.append(mask.reshape(layer.shape))

        total_kept += k
        total_params += n

    compression_ratio = total_params / max(total_kept, 1)
    logger.debug(
        "Top-K sparsify: kept %d/%d params (ratio=%.3f, compression=%.1f×)",
        total_kept, total_params, ratio, compression_ratio,
    )
    return sparse_weights, masks


def topk_reconstruct(
    sparse_weights: Weights,
    masks: List[np.ndarray],
) -> Weights:
    """Reconstruct dense weights from sparse representation + masks."""
    return [s * m for s, m in zip(sparse_weights, masks)]


# ---------------------------------------------------------------------------
# INT8 Quantization
# ---------------------------------------------------------------------------

class QuantizedLayer:
    """Stores quantized weight data with scale and zero_point for dequantization."""

    __slots__ = ("data_int8", "scale", "zero_point", "shape", "dtype")

    def __init__(
        self,
        data_int8: np.ndarray,
        scale: float,
        zero_point: int,
        shape: tuple,
        dtype: np.dtype,
    ):
        self.data_int8 = data_int8
        self.scale = scale
        self.zero_point = zero_point
        self.shape = shape
        self.dtype = dtype


def quantize_layer(layer: np.ndarray, bits: int = 8) -> QuantizedLayer:
    """
    Symmetric linear quantization: float32 → int8.

    Quantization formula (Alistarh et al. 2018):
        scale = max(|w|) / (2^(bits-1) - 1)
        q(w) = round(w / scale)           [symmetric: zero_point = 0]
        w_hat = q(w) * scale              [dequantized]

    Args:
        layer: Float32 weight array.
        bits:  Quantization bits (8 → INT8).

    Returns:
        QuantizedLayer with INT8 data + scale factor.

    Communication saving: float32 (4B) → int8 (1B) = 4× reduction.
    """
    flat = layer.flatten()
    max_val = np.abs(flat).max()

    if max_val == 0.0:
        scale = 1.0
    else:
        qmax = 2 ** (bits - 1) - 1  # 127 for int8
        scale = float(max_val / qmax)

    quantized = np.round(flat / scale).astype(np.int8)
    return QuantizedLayer(
        data_int8=quantized,
        scale=scale,
        zero_point=0,  # symmetric quantization
        shape=layer.shape,
        dtype=layer.dtype,
    )


def dequantize_layer(ql: QuantizedLayer) -> np.ndarray:
    """Dequantize INT8 data back to float32: w_hat = q * scale."""
    return (ql.data_int8.astype(np.float32) * ql.scale).reshape(ql.shape)


def quantize_weights(weights: Weights, bits: int = 8) -> List[QuantizedLayer]:
    """Quantize all weight layers to INT8."""
    qw = [quantize_layer(layer, bits=bits) for layer in weights]
    total_bytes_orig = sum(layer.nbytes for layer in weights)
    total_bytes_comp = sum(ql.data_int8.nbytes for ql in qw)
    logger.debug(
        "INT8 quantization: %.2f KB → %.2f KB (%.1f× reduction)",
        total_bytes_orig / 1024,
        total_bytes_comp / 1024,
        total_bytes_orig / max(total_bytes_comp, 1),
    )
    return qw


def dequantize_weights(quantized: List[QuantizedLayer]) -> Weights:
    """Dequantize all layers back to float32."""
    return [dequantize_layer(ql) for ql in quantized]


# ---------------------------------------------------------------------------
# High-Level Compression / Decompression API
# ---------------------------------------------------------------------------

class GradientCompressor:
    """
    Unified gradient compressor supporting multiple strategies.

    Strategies:
      "topk"      — Top-K sparsification with error feedback
      "quantize"  — INT8 quantization
      "both"      — Top-K then quantize non-zero values
      "none"      — No compression (passthrough)

    Usage:
        compressor = GradientCompressor(strategy="topk", topk_ratio=0.10)
        payload, meta = compressor.compress(delta_weights)
        reconstructed = compressor.decompress(payload, meta)
        compressor.update_error_feedback(original_weights, reconstructed)
    """

    def __init__(
        self,
        strategy: str = "topk",
        topk_ratio: float = 0.10,
        quantize_bits: int = 8,
        error_feedback: bool = True,
        client_id: str = "unknown",
    ):
        self.strategy = strategy
        self.topk_ratio = topk_ratio
        self.quantize_bits = quantize_bits
        self.error_feedback = error_feedback
        self.client_id = client_id
        self._error_buffer = ErrorFeedbackBuffer()
        self._round_stats: Dict = {}

    def compress(
        self,
        weight_deltas: Weights,
    ) -> Tuple[object, dict]:
        """
        Compress weight deltas for transmission.

        Returns:
            (payload, metadata): payload is the compressed representation,
                                 metadata is needed for decompression.
        """
        # Apply error feedback: add accumulated residual before compressing
        if self.error_feedback and self.strategy != "none":
            deltas_to_compress = self._error_buffer.add_to_weights(weight_deltas)
        else:
            deltas_to_compress = weight_deltas

        original_bytes = sum(d.nbytes for d in deltas_to_compress)

        if self.strategy == "topk":
            sparse, masks = topk_sparsify(deltas_to_compress, ratio=self.topk_ratio)
            payload = {"sparse": sparse, "masks": masks}
            meta = {"strategy": "topk"}
            kept = sum(int(np.count_nonzero(mask)) for mask in masks)
            # Estimated sparse wire format: float value + int32 flat index.
            compressed_bytes = kept * (4 + 4)

        elif self.strategy == "quantize":
            quantized = quantize_weights(deltas_to_compress, bits=self.quantize_bits)
            payload = {"quantized": quantized}
            meta = {"strategy": "quantize"}
            compressed_bytes = sum(
                ql.data_int8.nbytes + 8 + 4 * len(ql.shape)
                for ql in quantized
            )

        elif self.strategy == "both":
            sparse, masks = topk_sparsify(deltas_to_compress, ratio=self.topk_ratio)
            quantized = quantize_weights(sparse, bits=self.quantize_bits)
            payload = {"quantized": quantized, "masks": masks}
            meta = {"strategy": "both"}
            kept = sum(int(np.count_nonzero(mask)) for mask in masks)
            # Estimated sparse wire format: int8 value + int32 flat index.
            compressed_bytes = kept * (1 + 4) + 8 * len(quantized)

        else:  # "none"
            payload = {"weights": weight_deltas}
            meta = {"strategy": "none"}
            compressed_bytes = original_bytes

        ratio = original_bytes / max(compressed_bytes, 1)
        self._round_stats = {
            "strategy": self.strategy,
            "original_bytes": original_bytes,
            "compressed_bytes": compressed_bytes,
            "compression_ratio": round(ratio, 2),
            # Flower still receives reconstructed dense ndarrays. These figures
            # estimate a future sparse/quantized codec rather than measured wire bytes.
            "wire_compression_applied": False,
        }
        logger.info(
            "[%s] Simulated compression: %s | %.2f KB → est. %.2f KB (%.1f×)",
            self.client_id, self.strategy,
            original_bytes / 1024, compressed_bytes / 1024, ratio,
        )
        return payload, meta

    def decompress(self, payload: dict, meta: dict) -> Weights:
        """Reconstruct full weight deltas from compressed payload."""
        strategy = meta.get("strategy", self.strategy)

        if strategy == "topk":
            return topk_reconstruct(payload["sparse"], payload["masks"])
        elif strategy == "quantize":
            return dequantize_weights(payload["quantized"])
        elif strategy == "both":
            dequantized = dequantize_weights(payload["quantized"])
            return topk_reconstruct(dequantized, payload["masks"])
        else:
            return payload["weights"]

    def update_error_feedback(
        self,
        original_deltas: Weights,
        reconstructed: Weights,
    ) -> None:
        """Update error feedback buffer after a round."""
        if self.error_feedback and self.strategy != "none":
            self._error_buffer.update(original_deltas, reconstructed)

    def get_round_stats(self) -> dict:
        """Return compression statistics from the last round."""
        return self._round_stats.copy()


# ---------------------------------------------------------------------------
# Communication Cost Estimation
# ---------------------------------------------------------------------------

def estimate_communication_bytes(weights: Weights) -> dict:
    """
    Estimate communication cost for different strategies.

    Returns a dict with bytes and equivalent savings for logging/benchmarking.
    """
    full_bytes = sum(w.nbytes for w in weights)
    n_params = sum(w.size for w in weights)

    return {
        "uncompressed_bytes": full_bytes,
        "topk_10pct_bytes": int(full_bytes * 0.10),   # ~10× reduction
        "quantize_int8_bytes": full_bytes // 4,         # ~4× reduction
        "combined_bytes": int(full_bytes * 0.10) // 4, # ~40× reduction
        "total_parameters": n_params,
    }


# ---------------------------------------------------------------------------
# Unit-test hooks
# ---------------------------------------------------------------------------

def _test_topk_compression():
    """Verify Top-K sparsification retains exactly K% of values."""
    weights = [np.random.randn(100, 50).astype(np.float32),
               np.random.randn(50).astype(np.float32)]

    sparse, masks = topk_sparsify(weights, ratio=0.10)
    reconstructed = topk_reconstruct(sparse, masks)

    for s, m in zip(sparse, masks):
        nonzero_count = np.count_nonzero(s)
        total = s.size
        actual_ratio = nonzero_count / total
        assert 0.08 <= actual_ratio <= 0.12, f"Expected ~10%, got {actual_ratio:.2%}"

    for orig, recon in zip(weights, reconstructed):
        assert orig.shape == recon.shape
    print("[PASS] Top-K sparsification: shape preserved, correct sparsity")


def _test_quantization():
    """Verify INT8 quantization round-trip is close to original."""
    layer = np.random.randn(64, 32).astype(np.float32) * 5.0
    quantized = quantize_layer(layer)
    reconstructed = dequantize_layer(quantized)

    max_error = np.abs(layer - reconstructed).max()
    # Max relative error for 8-bit symmetric: ~0.4% of range
    assert max_error < 0.1 * np.abs(layer).max(), f"Quantization error too large: {max_error}"
    assert quantized.data_int8.dtype == np.int8
    print(f"[PASS] INT8 quantization: max_error={max_error:.6f}")


def _test_error_feedback():
    """
    Verify error feedback: decompress(compress(g + e)) + new_e recovers g over time.
    The key property is that g_approx + e_new = g + e_old (exact round-trip).
    """
    rng = np.random.default_rng(seed=0)
    weights = [rng.standard_normal(20).astype(np.float32)]
    compressor = GradientCompressor(strategy="topk", topk_ratio=0.20, error_feedback=True)

    # Run one round manually and verify the error-feedback invariant:
    # (g + e_old) = decompress(compress(g + e_old)) + e_new
    g = weights[0]

    # Initialize buffer
    compressor._error_buffer.initialize(weights)
    e_old = compressor._error_buffer.get_residuals()[0]

    payload, meta = compressor.compress(weights)
    recon = compressor.decompress(payload, meta)
    compressor.update_error_feedback(weights, recon)
    e_new = compressor._error_buffer.get_residuals()[0]

    # Key invariant: (g + e_old) ≈ recon[0] + e_new  (up to float32 precision)
    lhs = g + e_old
    rhs = recon[0] + e_new
    assert np.allclose(lhs, rhs, atol=1e-5), (
        f"Error feedback invariant violated: max_diff={np.abs(lhs-rhs).max():.6f}"
    )
    # Also: buffer should be initialized and non-empty
    assert len(compressor._error_buffer.get_residuals()) > 0
    print(f"[PASS] Error feedback: invariant (g+e_old == recon+e_new) holds, "
          f"max_diff={np.abs(lhs-rhs).max():.2e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    _test_topk_compression()
    _test_quantization()
    _test_error_feedback()
    print("All fl/compression.py tests passed.")
