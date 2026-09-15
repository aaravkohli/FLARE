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
    assert "port_receive_drop_window" not in with_port["supported_signals"]
    store.update_port_observation(
        "drone_1", received_packets=120, dropped_packets=5,
        error_packets=1, timestamp=101.2,
    )
    window = store.snapshot("drone_1", now=101.3)
    assert window["controller_port_window_rx_packets"] == 20
    assert window["controller_port_window_dropped_packets"] == 3
    assert window["controller_port_window_error_packets"] == 0
    assert window["port_window_seconds"] == 1.0
    assert "port_receive_drop_window" in window["supported_signals"]
    store.update_port_observation(
        "drone_1", received_packets=1, dropped_packets=0,
        error_packets=0, timestamp=102.2,
    )
    reset = store.snapshot("drone_1", now=102.3)
    assert "port_window_seconds" not in reset
    assert "port_receive_drop_window" not in reset["supported_signals"]
    stale = store.snapshot("drone_1", now=106.0)
    assert "controller_dropped_packets" not in stale
    assert "port_receive_drop_window" not in stale["supported_signals"]


def test_flow_window_is_withheld_on_first_poll_and_rule_counter_reset() -> None:
    store = ControllerEvidenceStore(source="ryu_openflow", independent=True)
    store.update_flow_observation(
        "drone_1", received_packets=100, forwarded_packets=100,
        dropped_packets=0, observed_source_mac="00:00:00:00:00:02",
        timestamp=100.0,
    )
    first = store.snapshot("drone_1", now=100.1)
    assert first["flow_totals_scope"] == "installed_rule_cumulative"
    assert "flow_window_seconds" not in first
    assert "packet_rate_per_s" not in first
    store.update_flow_observation(
        "drone_1", received_packets=150, forwarded_packets=145,
        dropped_packets=5, observed_source_mac="00:00:00:00:00:02",
        timestamp=101.0,
    )
    valid = store.snapshot("drone_1", now=101.1)
    assert valid["controller_flow_window_rx_packets"] == 50
    assert valid["controller_flow_window_forwarded_packets"] == 45
    assert valid["controller_flow_window_policy_dropped_packets"] == 5
    assert valid["flow_window_start"] == 100.0
    assert valid["packet_rate_per_s"] == 50.0
    store.update_flow_observation(
        "drone_1", received_packets=10, forwarded_packets=0,
        dropped_packets=10, observed_source_mac="00:00:00:00:00:02",
        timestamp=102.0,
    )
    reset = store.snapshot("drone_1", now=102.1)
    assert "flow_window_seconds" not in reset
    assert "packet_rate_per_s" not in reset
    store.update_flow_observation(
        "drone_1", received_packets=30, forwarded_packets=0,
        dropped_packets=30, observed_source_mac="00:00:00:00:00:02",
        timestamp=103.0,
    )
    hold = store.snapshot("drone_1", now=103.1)
    assert hold["controller_flow_window_policy_dropped_packets"] == 20
    assert hold["controller_flow_window_forwarded_packets"] == 0
