"""Controller DoS-rate evidence must represent PacketIn, not operator commands."""

from sdn.evidence import ControllerEvidenceStore


def test_packet_in_rate_has_explicit_observer_and_one_second_window() -> None:
    store = ControllerEvidenceStore(source="ryu_openflow", independent=True)
    store.update_flow_observation(
        "drone_1", received_packets=100, forwarded_packets=100,
        dropped_packets=0, observed_source_mac="00:00:00:00:00:02",
        timestamp=100.0,
    )
    before = store.snapshot("drone_1", now=100.5)
    assert before["control_messages_per_s"] == 0.0
    assert before["control_rate_observer"] == "access_port_packet_in"
    store.record_control_message("drone_1", timestamp=100.6)
    during = store.snapshot("drone_1", now=100.7)
    assert during["control_messages_per_s"] == 1.0
    after = store.snapshot("drone_1", now=102.0)
    assert after["control_messages_per_s"] == 0.0


def test_ingress_port_loss_is_separate_from_installed_policy_drop() -> None:
    store = ControllerEvidenceStore(source="ryu_openflow", independent=True)
    store.update_flow_observation(
        "drone_1", received_packets=100, forwarded_packets=0,
        dropped_packets=100, observed_source_mac="00:00:00:00:00:02",
        timestamp=100.0,
    )
    without_port = store.snapshot("drone_1", now=100.1)
    assert without_port["controller_policy_dropped_packets"] == 100
    assert "controller_dropped_packets" not in without_port
    assert "port_receive_drop_counters" not in without_port["supported_signals"]
    store.update_port_observation(
        "drone_1", received_packets=100, dropped_packets=2,
        error_packets=1, timestamp=100.2,
    )
    with_port = store.snapshot("drone_1", now=100.3)
    assert with_port["controller_policy_dropped_packets"] == 100
    assert with_port["controller_dropped_packets"] == 3
    assert with_port["drop_counter_semantics"] == "ingress_port_receive_drop_error"
    assert "port_receive_drop_counters" in with_port["supported_signals"]
    stale = store.snapshot("drone_1", now=104.0)
    assert "controller_dropped_packets" not in stale
    assert "port_receive_drop_counters" not in stale["supported_signals"]
