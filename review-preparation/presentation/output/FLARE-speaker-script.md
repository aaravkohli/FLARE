# FLARE panel presentation

Ten slides for approximately five minutes. Speaker notes in the PowerPoint contain the same script, followed by optional detail and project sources.

## Slide 1: Introduction and motivation

**Time: 0:00–0:25**

FLARE stands for Federated Learning and Adaptive Rerouting Engine. We built a research prototype to study resilient communication in UAV networks. Imagine drones inspecting a disaster area while communication conditions change. Useful sensing matters only if the observations can reach the operator. Our demonstration uses software UAVs and modeled packets.

<details>
<summary>Follow-up detail and sources</summary>

This is software simulation, not physical flight control or a satellite deployment. Intended applications include inspection, search and rescue, and environmental monitoring.

Sources: review-preparation/README.md; simulation/swarm/engine.py; review-preparation/evidence/01-ring-paused.png

</details>

## Slide 2: Problem and existing approaches

**Time: 0:25–0:55**

We focus on two failure paths. Interference can make a communication option unreliable. A compromised participant can also submit misleading traffic or a harmful training update. A fixed route adapts poorly when its link changes. Choosing only the lowest threat ignores delay and frequent switching. Ordinary federated averaging can give harmful updates too much influence. These are our comparison points, rather than a claim about every existing system.

<details>
<summary>Follow-up detail and sources</summary>

The contribution is an integrated and inspectable workflow. We do not claim to have invented federated learning, BiLSTM, DQN or SDN, or to have established novelty through a new systematic literature review.

Sources: rl/evaluation.py; rl/reward.py; fl/aggregator.py; fl/trust.py

</details>

## Slide 3: Proposed solution and architecture

**Time: 0:55–1:35**

FLARE connects observation, threat assessment, adaptive policy, a safety check and confirmed forwarding. The Python simulator owns network state and packet outcomes. A BiLSTM reads recent measurements. DQN proposes a communication family, and deterministic safety rules constrain that proposal. The network engine separately computes the hops, while the controller confirms what it applied. FastAPI connects these services to the React workspace. Flower manages local training and aggregation. A completed round creates a candidate model, not an automatic deployment.

<details>
<summary>Follow-up detail and sources</summary>

The current v3 contract supports HOLD and an 18-value observation. Retained v2 has three route actions and a different all-unsafe fallback. Local SDN confirmation is logical simulator enforcement, not physical OpenFlow packet evidence.

Sources: simulation/swarm/engine.py; fl/model.py; rl/agent.py; rl/safety.py; orchestrator/loop.py; api/server.py; sdn/mock_sdn.py; fl/server.py

</details>

## Slide 4: Major modules and responsibilities

**Time: 1:35–2:05**

The detector reads signal strength, delivery ratio, signal-to-interference ratio, latency and packet loss over a recent window. This is like reading a short history instead of one photograph. The routing policy acts as a dispatcher choosing a communication option. The network finds the feasible hops. Safety rules provide a final permission check. Separately, the learning server checks update consistency and trust, so every contributor does not automatically receive equal influence.

<details>
<summary>Follow-up detail and sources</summary>

Technologies: Python 3.11, PyTorch, Flower, Stable-Baselines3 DQN, FastAPI, React/TypeScript, Three.js and SQLite. Bidirectional processing stays within the already-observed sequence window. The model does not read future mission telemetry.

Sources: fl/model.py; fl/trust.py; fl/aggregator.py; rl/agent.py; rl/safety.py; simulation/swarm/network.py; frontend-react/src/mission-simulation/SwarmCommandCenter.tsx

</details>

## Slide 5: Federated learning and update protection

**Time: 2:05–2:30**

Participants train locally and share model parameters. Before combining them, the server checks their size, direction, deviation and historical trust. Suspicious updates can receive less weight or be rejected. This screenshot shows the copied-model protection test rejecting a deliberately harmful update while leaving the deployed model unchanged. It verifies that specific protection path. It does not establish field detection accuracy or guarantee privacy by itself.

<details>
<summary>Follow-up detail and sources</summary>

The displayed damage reduction compares parameter distance to a clean control in this synthetic self-test. Do not call that number detector accuracy. Training and inference are separate. Advanced asynchronous/selection research settings do not yet demonstrate every advertised live effect.

Sources: fl/client.py; fl/trust.py; fl/aggregator.py; review-preparation/evidence/07-update-gate.png; review-preparation/evidence/demo-result.json

</details>

## Slide 6: Working process and live demonstration

**Time: 2:30–3:05**

In our rehearsal, a packet traveled from UAV-001 to UAV-006 through five Ring links. The backend recorded delivery after about 48 milliseconds of simulation time, with no retries. We then injected a fifteen-second Direct jam. We inspected the impairment, threat evidence and confirmed forwarding separately. Satellite was already installed before this injection, so continuing to use that alternative was the observed behavior. Pausing freezes the attack timer. Expiry ends the injected condition, while historical observations may remain.

<details>
<summary>Follow-up detail and sources</summary>

Recorded probe p1: 0.000000 to 0.04815747959331477 simulation seconds, five completed hops, 512 bytes, delivered. Never announce a Direct-to-Satellite switch unless the current runtime actually records that transition. A highlighted route alone does not prove packet delivery.

Sources: review-preparation/evidence/demo-probe-result.json; review-preparation/evidence/02-multihop-candidate.png; review-preparation/evidence/demo-rehearsal.json; review-preparation/evidence/demo-result.json

</details>

## Slide 7: Features and contribution

**Time: 3:05–3:30**

The workspace offers ten topologies, two views of the same state, packet probes, replay and synchronized comparison. Topology and flight formation are independent, so changing the connection pattern does not move the drones. A/B experiments share external inputs and use a deterministic mesh reference policy. They exclude live DQN and satellite choices so that we can study topology fairly. The contribution is this connected workflow and its inspectable evidence trail.

<details>
<summary>Follow-up detail and sources</summary>

Ten primary configurations: Full Mesh, Partial Mesh, Star, Ring, Tree, Cluster Mesh, Grid, Random Geometric, Hybrid Mesh and Self-healing Mesh. Comparisons exclude ground-control transit between UAVs. The 3D site scenery is illustrative.

Sources: frontend-react/src/mission-simulation/SwarmCommandCenter.tsx; frontend-react/src/mission-simulation/ExperimentPanel.tsx; simulation/swarm/topology.py; simulation/swarm/experiments.py; review-preparation/evidence/05-live-decision.png

</details>

## Slide 8: Topology experiment results

**Time: 3:30–4:00**

These are measured packet outcomes from one seed of our topology benchmark. Full Mesh delivered 96 percent of the measured cohort with about 9.84 milliseconds average latency. Partial Mesh delivered about 99.88 percent, with average latency around 38.50 milliseconds. That illustrates a delivery and delay trade-off under this configuration. It does not establish a universal winner. We also ran seeds seven and ninety-nine and retained the per-run measurements.

<details>
<summary>Follow-up detail and sources</summary>

The full benchmark has 30 runs across ten topologies and seeds 7/42/99. Current implementation measures offered packets over simulation interval [5,60), a 55-second cohort, then drains TTL. Do not describe that as 60 measured seconds after warmup. These values exclude model inference. Reproducible result hash: 5f3c515883dc132929edce274307804757997ad178d9b3379c7c91bb63b0a98d.

Sources: review-preparation/evidence/mesh-results.json; simulation/swarm/experiments.py

</details>

## Slide 9: Routing experiment results

**Time: 4:00–4:30**

This is a separate synthetic routing evaluation using our retained v2 checkpoint. Across ten paired episodes, DQN with safety achieved mean utility of 232.32, compared with 209.59 for lowest-threat routing and 119.94 for static Direct. The myopic utility baseline scored 231.79, which is close to DQN. We therefore do not claim DQN is universally best. This utility combines the experiment’s communication costs. It is not a percentage or a detection accuracy.

<details>
<summary>Follow-up detail and sources</summary>

Protocol: DronePathEnv, routing_qos_v2, base seed 42000, 10 episodes, 500 steps each, threshold 0.8, least-risk fallback when all routes unsafe. This retained v2 evaluation differs from current v3 live HOLD behavior. Never compare v2 and v3 reward magnitudes as if they share one scale.

Sources: review-preparation/evidence/routing-evaluation.json; rl/evaluation.py

</details>

## Slide 10: Conclusion, limitations and future scope

**Time: 4:30–5:00**

FLARE demonstrates an end-to-end workflow in controlled simulation. We can configure a condition, inspect measured evidence, examine the policy and verify the applied state. We do not claim physical drone validation or representative field accuracy. Important next steps are better radio data, confidence calibration, and independent wireless and packet-enforcement tests. Some advanced federation settings remain research paths. The main outcome is a reproducible platform for investigating resilience and explaining what the system actually did.

<details>
<summary>Follow-up detail and sources</summary>

The exhaustive application audit remains unfinished and has unresolved browser failures. Docker/Ryu/Mininet and ns-3 physical/emulated gates remain blocked by unavailable infrastructure. Rendering FPS met local targets, but 100-UAV simulation time lagged wall time. Do not describe the whole application or all research branches as passing.

Sources: review-preparation/evidence/review-facts.json; review-preparation/README.md; scripts/audit_support.py

</details>
