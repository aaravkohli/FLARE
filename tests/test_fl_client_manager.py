"""Fleet-aware Flower client manager tests."""

from __future__ import annotations

from pathlib import Path

from fl.client_manager import FleetClientManager
from fleet.registry import register_drone, update_drone


class FakeProcess:
    next_pid = 1000

    def __init__(self, command, **_kwargs):
        self.command = command
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _manager(tmp_path, *, real=False, require_real_data=False):
    return FleetClientManager(
        server_address="fl-server:8090",
        real=real,
        require_real_data=require_real_data,
        processed_dir=tmp_path / "processed",
        status_path=tmp_path / "status.json",
        reconcile_interval_s=0.2,
        popen=FakeProcess,
    )


def test_manager_starts_one_distinct_process_per_registry_identity(tmp_path):
    manager = _manager(tmp_path)
    first = manager.reconcile_once()
    assert set(first["clients"]) == {"drone_1", "drone_2", "drone_3"}
    assert all(row["status"] == "active" for row in first["clients"].values())

    register_drone(
        drone_id="survey_4",
        display_name="Survey 4",
        mac="02:00:00:00:00:44",
        access_port=7,
    )
    register_drone(
        drone_id="relay_alpha",
        display_name="Relay Alpha",
        mac="02:00:00:00:00:55",
        access_port=8,
    )
    second = manager.reconcile_once()
    assert set(second["clients"]) == {
        "drone_1", "drone_2", "drone_3", "survey_4", "relay_alpha",
    }
    survey_command = second["clients"]["survey_4"]["command"]
    assert survey_command[survey_command.index("--client_id") + 1] == "survey_4"
    manager.stop()


def test_real_manager_never_starts_a_client_without_its_own_partition(tmp_path):
    manager = _manager(tmp_path, real=True, require_real_data=True)
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "drone_1_train.csv").write_text("rssi\n-50\n")
    status = manager.reconcile_once()

    assert status["clients"]["drone_1"]["status"] == "active"
    assert status["clients"]["drone_2"]["status"] == "real_data_unavailable"
    assert "drone_2" not in manager._processes
    assert manager.dataset_path("drone_2").name == "drone_2_train.csv"
    manager.stop()


def test_disabled_identity_stops_its_client_and_reenable_starts_a_new_one(tmp_path):
    manager = _manager(tmp_path)
    first = manager.reconcile_once()
    old_process = manager._processes["drone_3"]
    old_pid = first["clients"]["drone_3"]["pid"]
    update_drone("drone_3", enabled=False)
    disabled = manager.reconcile_once()
    assert "drone_3" not in disabled["clients"]
    assert "drone_3" not in manager._processes
    assert old_process.poll() == 0
    update_drone("drone_3", enabled=True)
    restored = manager.reconcile_once()
    assert restored["clients"]["drone_3"]["pid"] != old_pid
    assert restored["clients"]["drone_3"]["status"] == "active"
    manager.stop()
