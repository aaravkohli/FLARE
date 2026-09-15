"""Network evidence, DoS/spoofing detection, and real-mode fail-closed tests."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import orchestrator.loop as orchestrator
from security.network_detector import NetworkThreatAnalyzer
from security.adapters import merge_security_evidence
import sdn.mock_sdn as mock_sdn
from sdn.evidence import ControllerEvidenceStore
from scripts.run_network_security_experiment import run as run_network_study
import scripts.run_network_security_experiment as network_study
from fastapi.testclient import TestClient
from schemas.decision_event import DecisionEvent


def _config() -> dict:
    return orchestrator._SECURITY_CFG["network_detection"]


def _evidence(**overrides) -> dict:
    now = time.time()
    value = {
        "reported_tx_packets": 100,
        "controller_rx_packets": 100,
        "controller_forwarded_packets": 98,
        "reported_forwarded_packets": 98,
        "control_messages_per_s": 5.0,
        "duplicate_sequence_ratio": 0.01,
        "controller_dropped_packets": 2,
        "controller_policy_dropped_packets": 0,
        "controller_port_rx_packets": 100,
        "controller_port_dropped_packets": 2,
        "controller_port_error_packets": 0,
        "port_timestamp": now,
        "drop_counter_semantics": "ingress_port_receive_drop_error",
        "packet_rate_per_s": 50.0,
        "timestamp": now,
        "controller_timestamp": now,
        "observed_source_mac": "00:00:00:00:00:02",
        "expected_source_mac": "00:00:00:00:00:02",
        "provenance": {
            "category": "ryu_openflow",
            "observer_id": "controller-test",
            "independent": True,
            "collected_at": now,
            "age_s": 0.0,
        },
    }
    value.update(overrides)
    return value


def test_normal_controller_evidence_remains_normal():
    result = NetworkThreatAnalyzer(_config()).analyze(
        "drone_1", _evidence(), max_path_latency_ms=30.0,
        require_independent=True,
    )
    assert result.status == "NORMAL"
    assert result.detected_classes == ()


def test_dos_requires_corroborating_signals_and_requests_control_only():
    result = NetworkThreatAnalyzer(_config()).analyze(
        "drone_1",
        _evidence(
            controller_forwarded_packets=5,
            controller_dropped_packets=150,
            controller_port_dropped_packets=150,
            packet_rate_per_s=800.0,
        ),
        max_path_latency_ms=900.0,
        require_independent=True,
    )
    assert result.status == "MALICIOUS"
    assert "dos" in result.detected_classes
    assert result.response_hint == "control_only"


def test_installed_policy_drops_cannot_become_real_dos_evidence():
    result = NetworkThreatAnalyzer(_config()).analyze(
        "drone_1",
        _evidence(
            controller_forwarded_packets=0,
            reported_forwarded_packets=0,
            controller_policy_dropped_packets=100,
            controller_port_dropped_packets=0,
            controller_dropped_packets=0,
        ),
        max_path_latency_ms=30.0,
        require_independent=True,
    )
    assert result.status == "NORMAL"
    assert result.drop_score == 0.0
    assert "policy_drop_not_scored" in result.available_signals
    assert "port_receive_drop" in result.available_signals


def test_missing_real_port_statistics_are_unsupported_not_policy_drop_fallback():
    raw = _evidence(
        controller_forwarded_packets=0,
        reported_forwarded_packets=0,
        controller_policy_dropped_packets=100,
    )
    for key in (
        "controller_port_rx_packets", "controller_port_dropped_packets",
        "controller_port_error_packets", "port_timestamp",
        "controller_dropped_packets", "drop_counter_semantics",
    ):
        raw.pop(key)
    result = NetworkThreatAnalyzer(_config()).analyze(
        "drone_1", raw, max_path_latency_ms=30.0,
        require_independent=True,
    )
    assert result.status == "NORMAL"
    assert result.drop_score == 0.0
    assert "port_receive_drop" not in result.available_signals
    assert "policy_drop_not_scored" in result.available_signals
    assert NetworkThreatAnalyzer(_config()).analyze(
        "drone_1", {**raw, "controller_dropped_packets": 100},
        max_path_latency_ms=30.0, require_independent=True,
    ).reason == "port_drop_contract_invalid"


def test_authenticated_source_identity_mismatch_requests_quarantine():
    observed_at = time.time()
    result = NetworkThreatAnalyzer(_config()).analyze(
        "drone_1",
        _evidence(
            observed_source_mac="02:ff:ff:ff:ff:fe",
            identity_observed_at=observed_at,
        ),
        max_path_latency_ms=30.0,
        require_independent=True,
    )
    assert result.status == "MALICIOUS"
    assert result.identity_mismatch is True
    assert result.response_hint == "quarantined"


def test_real_identity_mismatch_without_fresh_packet_in_is_unavailable():
    analyzer = NetworkThreatAnalyzer(_config())
    unmatched = _evidence(observed_source_mac="02:ff:ff:ff:ff:fe")
    assert analyzer.analyze(
        "drone_1", unmatched, max_path_latency_ms=30.0,
        require_independent=True,
    ).reason == "identity_observation_untrusted"
    stale = _evidence(
        observed_source_mac="02:ff:ff:ff:ff:fe",
        identity_observed_at=time.time() - 10.0,
    )
    assert analyzer.analyze(
        "drone_1", stale, max_path_latency_ms=30.0,
        require_independent=True,
    ).status == "UNAVAILABLE"


def test_controller_store_preserves_recent_access_port_identity_observation():
    store = ControllerEvidenceStore(source="ryu_openflow", independent=True)
    store.update_flow_observation(
        "drone_1",
        received_packets=10,
        forwarded_packets=9,
        dropped_packets=1,
        observed_source_mac="00:00:00:00:00:02",
        timestamp=100.0,
    )
    store.update_source_identity(
        "drone_1", "02:ff:ff:ff:ff:fe", timestamp=100.5
    )
    snapshot = store.snapshot("drone_1", now=101.0)
    assert snapshot["observed_source_mac"] == "02:ff:ff:ff:ff:fe"
    assert snapshot["identity_observed_at"] == 100.5
    assert "source_mac" in snapshot["supported_signals"]


def test_expected_flow_mac_is_not_claimed_as_observed_identity():
    store = ControllerEvidenceStore(source="ryu_openflow", independent=True)
    store.update_flow_observation(
        "drone_1", received_packets=10, forwarded_packets=10,
        dropped_packets=0, observed_source_mac="00:00:00:00:00:02",
        timestamp=100.0,
    )
    snapshot = store.snapshot("drone_1", now=101.0)
    assert snapshot["identity_observed_at"] is None
    assert "source_mac" not in snapshot["supported_signals"]


def test_timestamp_join_rejects_misaligned_or_malformed_counter_evidence():
    now = time.time()
    reported = {
        "reported_tx_packets": 100,
        "reported_forwarded_packets": 98,
        "timestamp": now,
    }
    controller = {
        "controller_rx_packets": 100,
        "controller_forwarded_packets": 98,
        "control_messages_per_s": 2.0,
        "controller_timestamp": now + 0.2,
        "identity_observed_at": now,
        "control_rate_observer": "access_port_packet_in",
    }
    joined = merge_security_evidence(
        reported, controller, category="ryu_openflow",
        observer_id="controller-test", independent=True,
    )
    assert joined is not None
    assert joined["identity_observed_at"] == now
    assert joined["control_rate_observer"] == "access_port_packet_in"
    assert merge_security_evidence(
        reported, {**controller, "controller_timestamp": now + 5.0},
        category="ryu_openflow", observer_id="controller-test",
        independent=True,
    ) is None
    assert merge_security_evidence(
        {**reported, "reported_tx_packets": "NaN"}, controller,
        category="ryu_openflow", observer_id="controller-test",
        independent=True,
    ) is None
    assert merge_security_evidence(
        reported, controller, category="ryu_openflow",
        observer_id="controller-test", independent=True,
        max_join_skew_s=float("nan"),
    ) is None


def test_scenario_label_is_not_a_detector_feature():
    first = NetworkThreatAnalyzer({**_config(), "history_alpha": 0.0}).analyze(
        "drone_1", _evidence(simulation_profile="normal"),
        max_path_latency_ms=30.0,
    )
    second = NetworkThreatAnalyzer({**_config(), "history_alpha": 0.0}).analyze(
        "drone_1", _evidence(simulation_profile="dos"),
        max_path_latency_ms=30.0,
    )
    first_payload = first.to_dict()
    second_payload = second.to_dict()
    # Wall-clock sampling can change freshness by a few microseconds; detector
    # decisions and scored evidence must remain identical when only the label
    # used by the evaluation harness changes.
    first_payload.pop("evidence_freshness")
    second_payload.pop("evidence_freshness")
    assert first_payload == second_payload


def test_untrusted_and_stale_real_evidence_are_unavailable():
    untrusted = _evidence()
    untrusted["provenance"] = {**untrusted["provenance"], "independent": False}
    analyzer = NetworkThreatAnalyzer(_config())
    assert analyzer.analyze(
        "drone_1", untrusted, max_path_latency_ms=30.0,
        require_independent=True,
    ).status == "UNAVAILABLE"
    stale = _evidence(controller_timestamp=time.time() - 10.0)
    assert analyzer.analyze(
        "drone_1", stale, max_path_latency_ms=30.0,
        require_independent=True, max_evidence_age_s=3.0,
    ).reason == "evidence_stale"
    future = _evidence(controller_timestamp=time.time() + 10.0)
    assert analyzer.analyze(
        "drone_1", future, max_path_latency_ms=30.0,
        require_independent=True,
    ).reason == "evidence_timestamp_future"


def _telemetry(drone_id: str) -> dict:
    return {
        "drone_id": drone_id,
        "timestamp": time.time(),
        "source": "live",
        "paths": [
            {
                "path_id": name,
                "rssi": -55.0,
                "pdr": 0.95,
                "sinr": 20.0,
                "latency": 20.0,
                "packet_loss": 0.02,
            }
            for name in ("direct", "satellite", "mesh")
        ],
    }


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_real_telemetry_uses_bounded_live_cache_then_becomes_unavailable(monkeypatch, make_orchestrator):
    class Client:
        fail = False

        async def get(self, url, **kwargs):
            if "/sdn/evidence/" in url:
                raise RuntimeError("controller unavailable")
            if self.fail:
                raise RuntimeError("sensor unavailable")
            return _Response(_telemetry(kwargs["params"]["drone_id"]))

    monkeypatch.setattr(orchestrator, "MODE", "real")
    runtime = make_orchestrator(["drone_1"])
    client = Client()

    fresh = asyncio.run(runtime._collect_metrics(client))
    assert fresh["drone_1"]["source"] == "live"
    client.fail = True
    cached = asyncio.run(runtime._collect_metrics(client))
    assert cached["drone_1"]["source"] == "cached_live"

    collected_at, payload = runtime._live_telemetry_cache["drone_1"]
    runtime._live_telemetry_cache["drone_1"] = (
        collected_at - orchestrator.REAL_TELEMETRY_CACHE_TTL_S - 1.0,
        payload,
    )
    unavailable = asyncio.run(runtime._collect_metrics(client))
    assert unavailable == {}
    assert runtime._telemetry_collection_status["drone_1"]["status"] == "unavailable"


def test_real_sensor_failure_never_calls_synthetic_generator(monkeypatch, make_orchestrator):
    class Client:
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("offline")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("real mode must not call synthetic telemetry")

    import simulation.generator as generator

    monkeypatch.setattr(orchestrator, "MODE", "real")
    monkeypatch.setattr(generator, "generate_metrics", forbidden)
    runtime = make_orchestrator(["drone_1"])
    result = asyncio.run(runtime._collect_metrics(Client()))
    assert result == {}


def test_real_collection_never_upgrades_mock_controller_provenance(monkeypatch, make_orchestrator):
    class Client:
        async def get(self, url, **kwargs):
            if "/sdn/evidence/" in url:
                return _Response({
                    "controller_rx_packets": 100,
                    "controller_forwarded_packets": 100,
                    "control_messages_per_s": 1.0,
                    "controller_timestamp": time.time(),
                    "source": "mock_sdn",
                    "independent": False,
                })
            payload = _telemetry(kwargs["params"]["drone_id"])
            payload["security_evidence"] = {
                "reported_tx_packets": 100,
                "reported_forwarded_packets": 100,
                "timestamp": time.time(),
            }
            return _Response(payload)

    monkeypatch.setattr(orchestrator, "MODE", "real")
    runtime = make_orchestrator(["drone_1"])
    collected = asyncio.run(runtime._collect_metrics(Client()))
    evidence = collected["drone_1"]["security_evidence"]
    assert evidence["provenance"]["category"] == "mock_sdn"
    assert evidence["provenance"]["independent"] is False
    analysis = NetworkThreatAnalyzer(_config()).analyze(
        "drone_1", evidence, max_path_latency_ms=20.0, require_independent=True,
    )
    assert analysis.status == "UNAVAILABLE"


def test_invalid_security_join_does_not_discard_valid_real_rf_telemetry(monkeypatch, make_orchestrator):
    class Client:
        async def get(self, url, **kwargs):
            if "/sdn/evidence/" in url:
                return _Response({
                    "controller_rx_packets": 100,
                    "controller_forwarded_packets": 100,
                    "control_messages_per_s": 1.0,
                    "controller_timestamp": time.time() - 10.0,
                    "source": "ryu_openflow",
                    "independent": True,
                })
            payload = _telemetry(kwargs["params"]["drone_id"])
            payload["security_evidence"] = {
                "reported_tx_packets": "malformed",
                "reported_forwarded_packets": 100,
                "timestamp": time.time(),
            }
            return _Response(payload)

    monkeypatch.setattr(orchestrator, "MODE", "real")
    runtime = make_orchestrator(["drone_1"])
    collected = asyncio.run(runtime._collect_metrics(Client()))
    assert collected["drone_1"]["source"] == "live"
    assert collected["drone_1"]["security_evidence"] is None
    assert runtime._telemetry_collection_status["drone_1"]["status"] == "fresh"


def test_stale_real_network_evidence_cannot_drive_insider_containment(monkeypatch, make_orchestrator):
    class ForbiddenInsider:
        def analyze(self, *_args, **_kwargs):
            raise AssertionError("stale evidence must not enter insider analysis")

    monkeypatch.setattr(orchestrator, "MODE", "real")
    runtime = make_orchestrator(["drone_1"], freeze_fleet=True)
    runtime._insider_analyzer = ForbiddenInsider()
    runtime._fl_trust_context = lambda: {}
    telemetry = _telemetry("drone_1")
    telemetry["security_evidence"] = _evidence(
        timestamp=time.time() - 10.0,
        controller_timestamp=time.time() - 10.0,
    )

    async def collect(_client):
        return {"drone_1": telemetry}

    async def route(_client, path_name, _drone_id):
        return {"status": "ok", "installed_path": path_name}

    rows = []
    runtime._collect_metrics = collect
    monkeypatch.setattr(orchestrator, "_push_to_sdn", route)
    monkeypatch.setattr(orchestrator, "_log_experiments", rows.extend)
    asyncio.run(runtime._run_step(SimpleNamespace()))
    assert len(rows) == 1
    event = DecisionEvent.model_validate_json(rows[0]["event_json"])
    assert event.network_security_analysis.status == "UNAVAILABLE"
    assert event.insider_analysis is None
    assert event.containment is None


def test_unavailable_real_telemetry_holds_without_running_inference(monkeypatch, make_orchestrator):
    class NeverCalledModel:
        def __call__(self, *_args, **_kwargs):
            raise AssertionError("inference must not run without telemetry")

    runtime = make_orchestrator(["drone_1"], freeze_fleet=True)
    runtime._fl_model = NeverCalledModel()

    async def no_metrics(_client):
        return {}

    held = []

    async def hold(_client, drone_ids):
        held.extend(drone_ids)

    runtime._collect_metrics = no_metrics
    runtime._fail_closed_unavailable_drones = hold
    asyncio.run(runtime._run_step(object()))
    assert held == ["drone_1"]


def test_mock_controller_evidence_is_explicitly_non_independent():
    response = TestClient(mock_sdn.app).get(
        "/sdn/evidence/drone_1",
        headers={"Authorization": f"Bearer {mock_sdn.SDN_API_TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["source"] == "mock_sdn"
    assert response.json()["independent"] is False
    assert response.json()["control_rate_observer"] == "mock_none"


def test_controller_evidence_endpoint_requires_authentication():
    response = TestClient(mock_sdn.app).get("/sdn/evidence/drone_1")
    assert response.status_code == 401


def test_frozen_controlled_detector_study_passes_promotion_thresholds():
    report = run_network_study(seeds=[7, 42, 99], samples_per_profile=10)
    assert report["evidence_category"] == "controlled_simulation"
    assert report["promotion_gate"]["passed"] is True
    assert report["binary_detection"]["precision"] >= 0.90
    assert report["binary_detection"]["recall"] >= 0.90
    assert report["binary_detection"]["unavailable_count"] == 0


def test_unavailable_evidence_cannot_improve_recall_or_pass_study_gate(monkeypatch):
    class StatusStub:
        def __init__(self, _config):
            pass

        def analyze(self, sample_id, _evidence, **_kwargs):
            if "_normal_" in sample_id:
                status = "NORMAL"
            elif "_dos_0" in sample_id:
                status = "UNAVAILABLE"
            else:
                status = "MALICIOUS"
            return SimpleNamespace(
                status=status,
                detected_classes=() if status == "UNAVAILABLE" else ("dos",),
                risk_score=None if status == "UNAVAILABLE" else 0.8,
            )

    monkeypatch.setattr(network_study, "NetworkThreatAnalyzer", StatusStub)
    report = network_study.run(seeds=[7], samples_per_profile=10)
    assert report["binary_detection"]["unavailable_count"] == 1
    assert report["binary_detection"]["false_negatives"] == 1
    assert report["binary_detection"]["recall"] >= 0.90
    assert report["promotion_gate"]["passed"] is False
    assert report["binary_detection"]["false_positive_rate"] <= 0.05
