# Your individual contribution: choose only what is true

The repository establishes what FLARE implements. It cannot establish which parts you personally designed or completed. Choose a statement below only if it describes your actual responsibility. Combine statements only when you owned both areas. Credit teammates for their parts.

These are complete speaking options rather than assigned credit. Replace an option with your own accurate description if none fits. If asked about authorship, distinguish implementation, integration, testing and assistance from tools or teammates.

## If you owned the Mission Workspace

> “My main responsibility was the Mission Workspace. I organized the controls around running the simulation, observing the network and investigating evidence. I worked on separating inspected UAVs from command targets and on displaying injected conditions, detector observations and installed routes as different facts. I integrated those interactions with the existing mission APIs.”

Be ready to explain one interaction end to end: selecting a UAV, sending a probe, handling a stale response, or preserving workspace state during navigation. Show only the code and tests you actually worked on.

Relevant implementation: [SwarmCommandCenter.tsx](../frontend-react/src/mission-simulation/SwarmCommandCenter.tsx), [NetworkInspector.tsx](../frontend-react/src/mission-simulation/NetworkInspector.tsx), [AttackLab.tsx](../frontend-react/src/mission-simulation/AttackLab.tsx).

## If you owned the simulator or networking

> “My main responsibility was the network simulator. I worked on how physical radio eligibility, topology rules and routing interact, and on recording packet progression and failures. I kept the simulation state authoritative so that the interface could show actual modeled outcomes. I also worked on separating topology changes from drone formation.”

Be ready to trace one packet through a queue, transmission, arrival, failure or retry. Explain the difference between a topology edge, an installed hop and a completed transmission. Do not claim hardware RF validation.

Relevant implementation: [engine.py](../simulation/swarm/engine.py), [network.py](../simulation/swarm/network.py), [topology.py](../simulation/swarm/topology.py).

## If you owned the detector or data pipeline

> “My main responsibility was the threat-detection pipeline. I worked on preparing communication measurements as sequence windows and on integrating the detector's outputs into the runtime. I also examined the data and model contracts so that the application could distinguish learned inference from a fallback. I report controlled results separately from field accuracy.”

Be ready to explain the five input features, sequence grouping, normalization and one output head. State exactly which model/data work you performed. Do not claim that public modulation or drone-presence labels are independently validated jam labels.

Relevant implementation: [model.py](../fl/model.py), [data.py](../fl/data.py), [preprocess.py](../datasets/preprocess.py).

## If you owned federated learning and update protection

> “My main responsibility was federated training and update protection. I worked on local training, parameter exchange and the checks used before aggregation. I focused on making suspicious updates lose influence or be rejected while keeping the resulting evidence visible. I distinguish a training candidate from the model actually deployed for inference.”

Be ready to explain one accepted, down-weighted or rejected update and how it affects aggregation. State the actual study protocol when quoting a result. Do not call a copied-model self-test a complete training run.

Relevant implementation: [client.py](../fl/client.py), [server.py](../fl/server.py), [trust.py](../fl/trust.py), [aggregator.py](../fl/aggregator.py).

## If you owned routing, orchestration or SDN integration

> “My main responsibility was connecting the routing policy to executable network behavior. I worked on the distinction between DQN's proposed communication family, the safety-constrained request and the controller's confirmed forwarding. I focused on keeping failures and unavailable evidence visible so that a request would not be presented as a successful installation.”

Be ready to show one decision, its safety reason and its acknowledgment. Explain which adapter you used. Local mock SDN applies logical simulator rules and does not establish real OpenFlow forwarding.

Relevant implementation: [agent.py](../rl/agent.py), [safety.py](../rl/safety.py), [loop.py](../orchestrator/loop.py), [mock_sdn.py](../sdn/mock_sdn.py).

## If you owned integration, testing or experiments

> “My main responsibility was integration and verification. I traced frontend actions to backend effects and checked that the displayed result matched authoritative state. I worked with isolated runs, packet evidence and seeded experiments so that tests would not change the operator's mission. I also documented failures and limits rather than reporting every rendered feature as working.”

Be ready to explain one failure you reproduced, one assertion that distinguishes an acknowledgment from an accepted request, or one fair-comparison control. Claim only tests or repairs you personally completed. The latest broad browser failures and blocked real-network gates remain part of the record.

Relevant implementation: [tests](../tests), [browser workflows](../frontend-react/e2e), [experiments.py](../simulation/swarm/experiments.py).

## How to finish the answer naturally

After the statement that fits, add one concrete example from your own work and name the teammate responsibilities accurately:

> “One decision I can explain in detail is how this part handles a failed request. My teammates handled the other modules, and we integrated them through the shared interfaces.”

Use that example only if it is true. It is better to describe a smaller contribution clearly than to claim ownership of every algorithm. If the panel points to code you did not write, say which teammate owned it and explain the interface you integrated with it.
