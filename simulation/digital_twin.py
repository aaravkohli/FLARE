"""
simulation/digital_twin.py — UAV Swarm Digital Twin Engine  [FLARE v2]

Provides a high-fidelity, synchronized virtual replica of the physical swarm.
The Twin ingests real-time telemetry, maintains state matrices (Topology, 
RF, Battery, Threats), and provides predictive simulation capabilities.
"""

import logging
import threading
import time
from typing import Dict, List, Optional
import numpy as np
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# We import the local generator for physical environment telemetry mirroring.
# In a real deployment, this would use a WebSocket client or MQTT subscriber.
from fleet.registry import active_drone_ids
from simulation.generator import generate_metrics

logger = logging.getLogger(__name__)

# Base Energy capacity per drone (in Joules or abstract units)
MAX_BATTERY = 10000.0

class SwarmStateRegistry:
    """
    Central state registry for the Digital Twin.
    Continuously mirrors the physical swarm telemetry.
    """
    
    def __init__(self, sync_interval: float = 0.1):
        self.sync_interval = sync_interval
        self._lock = threading.Lock()
        
        # Twin State
        self.drones_state: Dict[str, dict] = {
            d: {
                "battery": MAX_BATTERY,
                "metrics": None,
                "last_update": 0.0,
                "ew_status": None,
                "gps": None
            } for d in active_drone_ids()
        }
        
        # Analytics
        self.sync_latency_ms: float = 0.0
        
        self._running = False
        self._sync_thread: Optional[threading.Thread] = None

    def start_sync(self):
        """Start the background synchronization engine."""
        if self._running:
            return
        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()
        logger.info("[DigitalTwin] Synchronization Engine STARTED (interval=%.2fs).", self.sync_interval)

    def stop_sync(self):
        """Stop the synchronization engine."""
        self._running = False
        if self._sync_thread:
            self._sync_thread.join(timeout=1.0)
        logger.info("[DigitalTwin] Synchronization Engine STOPPED.")

    def _sync_loop(self):
        """
        Ingests real-time physical telemetry.
        Simulates network ingestion by pulling from the generator.
        """
        while self._running:
            start_t = time.time()
            
            with self._lock:
                for drone_id in active_drone_ids():
                    self.drones_state.setdefault(drone_id, {
                        "battery": MAX_BATTERY,
                        "metrics": None,
                        "last_update": 0.0,
                        "ew_status": None,
                        "gps": None,
                    })
                    # Ingest physical telemetry
                    metrics = generate_metrics(drone_id=drone_id)
                    
                    # Update Registry State
                    state = self.drones_state[drone_id]
                    state["metrics"] = metrics["paths"]
                    state["gps"] = metrics["gps"]
                    state["ew_status"] = metrics["ew_status"]
                    state["last_update"] = metrics["timestamp"]
                    
                    # Battery degrades dynamically based on current state (simulated physical drain)
                    # For mirroring, we assume a continuous drain. In the predictive model, action dictates drain.
                    state["battery"] = max(0.0, state["battery"] - 0.5)

            end_t = time.time()
            # Calculate synchronization latency (time to ingest and parse state)
            self.sync_latency_ms = (end_t - start_t) * 1000.0
            
            time.sleep(self.sync_interval)

    def get_state(self, drone_id: str) -> dict:
        """Fetch a snapshot of the mirrored state for a specific drone."""
        with self._lock:
            # Return a deep copy to avoid race conditions during predictive rollouts
            import copy
            return copy.deepcopy(self.drones_state.get(drone_id, {}))
    
    def predict_next_state(self, current_state: dict, action_path: str) -> dict:
        """
        Forward kinematics & RF predictive model for RL planning.
        Simulates one step into the future based on the given routing action.
        """
        import copy
        import random
        
        next_state = copy.deepcopy(current_state)
        
        # 1. Predict Battery Degradation
        # Different paths have different energy costs (direct=low, sat=med, mesh=high)
        energy_costs = {"direct": 1.5, "satellite": 3.0, "mesh": 4.5}
        cost = energy_costs.get(action_path, 2.0)
        next_state["battery"] = max(0.0, next_state["battery"] - cost)
        
        # 2. Predict Link Quality Dynamics (EMA smoothing)
        # If an EW threat is active, predict compounding degradation
        ew_active = next_state.get("ew_status", {}).get("active_attack")
        jammed_paths = next_state.get("ew_status", {}).get("jammed_paths", [])
        
        if next_state.get("metrics"):
            for p in next_state["metrics"]:
                if p["path_id"] in jammed_paths or ew_active == "barrage":
                    # Predict continued degraded state
                    p["pdr"] = max(0.0, p["pdr"] - random.uniform(0.01, 0.05))
                    p["latency"] += random.uniform(5.0, 50.0)
                else:
                    # Natural mean reversion for clean paths
                    p["pdr"] = min(1.0, p["pdr"] + random.uniform(0.0, 0.02))
                    
        return next_state


# Global Singleton for the application
twin_registry = SwarmStateRegistry()

if __name__ == "__main__":
    twin = SwarmStateRegistry()
    twin.start_sync()
    time.sleep(0.5)
    state = twin.get_state("drone_1")
    print(f"Mirrored State (Battery): {state['battery']}")
    print(f"Sync Latency: {twin.sync_latency_ms:.2f} ms")
    
    # Test Prediction
    next_s = twin.predict_next_state(state, "satellite")
    print(f"Predicted Battery (after Satellite route): {next_s['battery']}")
    
    twin.stop_sync()
