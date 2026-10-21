# FLARE panel questions and natural answers

Use the short answer first. Offer the deeper explanation only if asked. These answers describe the current repository and recorded evidence, not a production or hardware deployment. Results and protocol boundaries are in [RESULTS.md](RESULTS.md).

## Basic questions

### Q1. What is FLARE?

**Say:** “FLARE means Federated Learning and Adaptive Rerouting Engine. It is a research prototype for studying resilient UAV communication. It combines threat observations, adaptive communication choices, safety checks and confirmed forwarding, and protects collaborative learning from suspicious updates.”

**If asked for depth:** The present demonstration uses an authoritative Python packet simulator, a React workspace, model services and logical SDN enforcement. It does not control physical UAV flights.

### Q2. Why did you choose this problem?

**Say:** “A drone can collect useful information but still fail its mission if that information cannot reach the operator. We wanted to study communication disruption alongside the trust problem created when participants contribute to a shared model.”

**If asked for depth:** Rescue, inspection and environmental monitoring motivate the use case. They are intended applications rather than completed deployments. Personal motivation should reflect your own experience.

### Q3. What is the exact problem statement?

**Say:** “We want to maintain permitted communication when available options degrade, while limiting the influence of suspicious participants. We also want an operator to distinguish what was injected, what was observed, what was proposed and what was actually applied.”

**If asked for depth:** We study packet delivery, latency and recovery separately from detector quality and aggregation quality. A high value in one category does not validate the others.

### Q4. Why is a fixed route insufficient?

**Say:** “A fixed route keeps using the same option even when its quality changes. Adaptive selection can consider another available option, but switching also has a cost. That is why we compare against fixed and simple greedy policies.”

**If asked for depth:** Our static Direct baseline is an experimental comparison, not a characterization of every existing networking system. A topology change and a routing-family decision are different operations.

### Q5. What are Direct, Satellite and Mesh?

**Say:** “Direct is the UAV’s modeled connection to ground control. Satellite is a modeled relay transport. Mesh uses eligible neighboring UAVs to forward packets across multiple hops.”

**If asked for depth:** Satellite delay and geometry follow simulator assumptions. No real satellite service participates. Ground control can be a route endpoint but cannot silently relay a peer-to-peer UAV route.

### Q6. What does the simulator actually execute?

**Say:** “It owns UAV positions, physical radio eligibility, topology links, routes and individual packet events. It records attempts, arrivals, failures and expiry. The frontend displays these records rather than deciding whether a packet succeeded.”

**If asked for depth:** Fixed movement steps and precise packet event times coexist in one simulation clock. The radio model, queues and channel draws remain approximations of real networking.

### Q7. What are the main objectives?

**Say:** “We want to observe communication conditions, choose a permitted alternative when appropriate, protect collaborative learning and make the evidence reproducible. The interface should let us investigate a failure without confusing an animation with a delivered packet.”

**If asked for depth:** Ten-topology comparisons use a fixed reference policy so we can isolate the topology question from learned-policy differences.

### Q8. What can you show the panel that is particularly convincing?

**Say:** “A delivered five-hop packet probe, an attack that expires on the simulation clock, separate detector and controller evidence, and a copied-model update-protection test. Each has a concrete backend result that the panel can inspect.”

**If asked for depth:** After establishing those, show synchronized comparison and export provenance. The terrain is useful context, but not scientific evidence of radio accuracy.

### Q9. What is your contribution compared with existing work?

**Say:** “Our contribution is the integration and evidence trail: network execution, learning, security checks, adaptive policy, safety and enforcement in one inspectable prototype, with reproducible topology experiments.”

**If asked for depth:** We do not claim to have invented FL, BiLSTM, DQN or SDN, or that no prior system combines them. A formal novelty claim needs an explicit literature comparison.

### Q10. Did you test real drones?

**Say:** “No. The verified demonstration uses software UAVs and modeled packets. We have separate integration paths for network emulation and external sensors, but that does not constitute physical flight or over-the-air RF validation.”

**If asked for depth:** Docker/Mininet/Ryu packet enforcement and ns-3 wireless gates were blocked in the available environment. Simulation evidence cannot substitute for those gates.

### Q11. What is the simplest explanation of the architecture?

**Say:** “The simulator supplies measurements. A detector estimates risk. DQN proposes a communication family. Safety checks constrain it, and the network/controller compute and apply forwarding. The interface shows the observations and confirmation. Flower separately coordinates local learning.”

**If asked for depth:** A trained candidate does not automatically overwrite the deployed detector. The deployment handoff and model contract need validation.

### Q12. Which technologies did you use, and why?

**Say:** “Python brings together simulation and machine learning. PyTorch implements the sequence detector, Flower coordinates federated training, and DQN handles a small discrete action set. FastAPI connects the services to a React and TypeScript interface, with Three.js for the 3D view.”

**If asked for depth:** SQLite provides straightforward local persistence for this prototype. This does not mean every simulated UAV runs a separate physical database or inference service.

## Intermediate questions

### Q13. How does FLARE identify an attack?

**Say:** “It observes evidence rather than trusting the injection label. The BiLSTM estimates communication risk from recent measurements. Separate traffic checks look at available forwarding, rates, identity and replay evidence. The model-update analyzer examines submitted parameter changes.”

**If asked for depth:** A jam can increase RF risk while the traffic analyzer remains NORMAL. These modules answer different questions. A visible attack profile is not proof that the learned classifier recognized that exact class.

### Q14. Why use a sequence model?

**Say:** “A recent pattern can be more informative than one reading. The BiLSTM processes a short measurement window, so it can use changes over that window instead of making every decision from a single noisy sample.”

**If asked for depth:** Bidirectionality is within the already collected window. It does not give the online system access to future mission measurements. Comparative superiority still needs a representative held-out evaluation.

### Q15. Which measurements go into the detector?

**Say:** “Signal strength, packet delivery ratio, signal-to-interference-plus-noise ratio, latency and packet loss. They describe different aspects of communication. A strong signal alone does not mean packets are delivered reliably.”

**If asked for depth:** Inputs use the checkpoint’s normalization, sequence and path contracts. Missing measurements should remain unavailable rather than turning into invented zeroes.

### Q16. Why federated learning instead of centralized training?

**Say:** “It lets participants train locally and exchange model parameters rather than collecting all raw training windows at one place. It is a useful way to study learning across participants with different communication conditions.”

**If asked for depth:** FL has communication costs, heterogeneous data and poisoning risks. Parameter sharing alone does not guarantee privacy. Centralized learning remains a useful baseline where raw data collection is feasible.

### Q17. How do you defend against a malicious training participant?

**Say:** “We compare its update with the population and its own history, then use the resulting trust and deviation evidence to decide its influence. A suspicious update can be down-weighted or rejected instead of automatically entering an ordinary average.”

**If asked for depth:** Robust statistics, direction, norm and historical consistency support the decision. Legitimate non-IID data can also look unusual, so false flags and false rejections must be measured separately.

### Q18. What does trust weighting mean in plain language?

**Say:** “It is like giving less influence to evidence from an inconsistent source. The server updates a participant’s reputation from observed update consistency, then uses that reputation and the current analysis when aggregating.”

**If asked for depth:** Trust is not a cryptographic proof of honesty. An adaptive or colluding attacker may evade the checks. High trust yesterday is not permission to ignore a clearly harmful update today.

### Q19. Why DQN for routing?

**Say:** “Our policy has a small set of discrete choices, so DQN is a reasonable method to explore the trade-off between useful communication and costs such as delay, loss and switching. We evaluate it against simpler policies rather than assuming learning must win.”

**If asked for depth:** The retained v2 evaluation scored close to a myopic utility baseline. That result does not establish a long-horizon advantage. The current v3 contract adds security evidence and executable HOLD.

### Q20. Does DQN calculate the whole mesh path?

**Say:** “No. DQN chooses the communication family. The network engine computes feasible hops under the topology, physical links, trust and restrictions. The controller separately confirms installed forwarding.”

**If asked for depth:** A policy proposal, safety-constrained request, installed hops and a delivered packet are four different observations. A highlighted route is not an acknowledgment or packet receipt.

### Q21. What happens when every route is unsafe?

**Say:** “It depends on the loaded contract and safety configuration. The current v3 workflow supports Network HOLD, which suspends data forwarding. The retained v2 configuration can request the least-risk degraded route and explicitly report that no route meets its safety threshold.”

**If asked for depth:** Do not promise every checkpoint has four actions or identical fallback behavior. Inspect runtime contract, safety reason and confirmed controller state. Flight HOLD is a different command that stops modeled movement.

### Q22. Why does jamming Direct sometimes appear to affect Mesh too?

**Say:** “The injected family and the displayed measurement are different things. A Direct-specific jam should not simply mark Mesh jammed. Mesh can still change because of shared airtime, queues, movement or its own rolling losses. We inspect the active condition and actual packet reasons before assigning a cause.”

**If asked for depth:** Barrage affects all families, Sweep cycles families, and Reactive/Adaptive conditions follow their configured behavior. Detector risk is not the same quantity as measured RF loss.

### Q23. Why can a threat score remain high after the timer ends?

**Say:** “The timer ends the injected condition. Recent measurements and detector history remain, so a score may take time to change. A pre-existing poor route or retained containment can also remain after injection expiry.”

**If asked for depth:** Remaining duration uses `expires_at − simulation_time`. Pausing freezes it. Expiry does not erase history or force the policy back to Direct.

### Q24. Why are extra colored lines visible in a Ring or Tree?

**Say:** “The canvas can show the UAV topology, ground-control links, an installed forwarding route and an inspected candidate. Those are separate layers. Color can also describe link quality. We use the legend and Layers controls to inspect one relationship at a time.”

**If asked for depth:** A dashed candidate is a proposal, a solid installed path is forwarding evidence, and actual packet events show traffic. Topology changes preserve drone coordinates, so a Ring graph need not look like a circular flight formation.

## Technical questions

### Q25. Which topologies are implemented?

**Say:** “The primary selector offers Full Mesh, Partial Mesh, Star, Ring, Tree, Cluster Mesh, Grid, Random Geometric, Hybrid Mesh and Self-healing Mesh. Legacy Chain and MANET names remain accepted for compatibility.”

**If asked for depth:** Every structural edge still needs an eligible radio link. Ring, Tree and Grid keep their membership through failures rather than silently adding replacement structural edges. Leaders/cluster heads can be re-elected after discovery timeout.

### Q26. What does self-healing actually guarantee?

**Say:** “It can retain healthy neighbors and promote eligible alternatives or joining links after failures. It cannot reconnect two components that have no physically eligible radio link between them.”

**If asked for depth:** Distinguish route convergence from service recovery. The former records when a route becomes available; the latter needs successful delivery. A flow that never recovers remains unrecovered.

### Q27. How do packet timing and pause work?

**Say:** “Packet transmissions use precise event timestamps inside the simulation clock. Queuing, arrival, retry and expiry do not have to wait for a whole movement tick. Pausing stops simulation-time progress, so packet progression and attack expiry pause as well.”

**If asked for depth:** Fixed-step movement remains separate. Rendering and selected-packet replay have their own presentation timing and must not redefine packet latency or injection duration.

### Q28. What happens to packets when a route breaks?

**Say:** “Completed hops remain part of the record. Unreliable traffic on a broken route drops. Reliable traffic may retry from its current node within the configured retry and TTL limits, and a repaired route is recorded.”

**If asked for depth:** A route repair cannot undo a completed hop or guarantee delivery through a partition. Packet conservation checks distinguish delivered, dropped and still-pending packets.

### Q29. How are A/B experiments kept fair?

**Say:** “They share initial positions, movement, offered traffic, radio configuration, scenario schedule and clock. Both use the same deterministic trust-aware mesh reference routing, so the compared variable is topology rather than a changing learned policy.”

**If asked for depth:** Satellite transport and ground-control transit are excluded. Separate random streams and keyed channel draws prevent different topology event counts from silently changing shared external conditions. Experiments keep state separate from the live fleet and training controls.

### Q30. What exactly does the benchmark measure?

**Say:** “It evaluates offered packet cohorts and graph state, including delivery, latency, jitter, throughput, hops, components, failed routes and recovery. Per-seed rows stay visible, and exports retain configuration and provenance.”

**If asked for depth:** Current defaults use warmup 5 and duration 60, but implementation selects the cohort in [5,60), so there are 55 measured seconds before the final TTL drain. Do not repeat the UI wording as 60 measured seconds after warmup.

### Q31. Which datasets did you use?

**Say:** “The pipeline supports RadioML 2018.01A, DroneRF and synthetic traces. The public RF sources require derived communication features and proxy labels. They are not a complete measured history of all three route families under independently labeled attacks.”

**If asked for depth:** RadioML’s modulation/SNR examples are not automatically jamming ground truth. DroneRF presence labels do not by themselves validate all threat classes. We need representative synchronized radio measurements for stronger claims.

### Q32. How do you prevent data leakage?

**Say:** “The data pipeline groups sequences and keeps windows from crossing train/test groups, UAVs or paths. RL traces use separate episode identities and fingerprints for training and evaluation.”

**If asked for depth:** Those checks reduce specific leakage paths. They do not prove that derived RF labels represent field attacks or that an inferred source order is a true temporal measurement. Evaluation quality still depends on the data contract.

### Q33. What do the checkpoints establish?

**Say:** “They establish that a compatible model artifact exists and can load under its metadata contract. The detector metadata describes its sequence/features and output heads. The current routing checkpoint uses the v3 observation/action contract.”

**If asked for depth:** A checkpoint file, training timestep count or round number does not establish representative accuracy. Hashes and metadata identify the artifact; a suitable independent test set establishes performance.

### Q34. What is SDN doing here?

**Say:** “It separates a requested communication policy from the forwarding rules that a controller reports as applied. In this demo the mock SDN service enforces logical rules inside the packet simulator and returns confirmation.”

**If asked for depth:** The repository also has a Ryu/OpenFlow integration path. We cannot claim real packet enforcement from the local mock acknowledgment. The Mininet/Ryu gate needs its own working runtime and captured packet evidence.

### Q35. What happens if the controller disconnects?

**Say:** “The simulator distinguishes loss of the command link from radio networking. Undelivered commands can expire, and the interface must not claim acknowledgment. Existing modeled UAV networking can continue under its current rules.”

**If asked for depth:** This control models a command-link failure, not proof of distributed controller failover after a process crash. The prototype’s inference/orchestration services are host processes, not verified independent onboard computers on every drone.

### Q36. How do you keep stale requests from changing a new mission?

**Say:** “Mission requests and events carry session and revision information. A reset creates a new session. Stale requests must be rejected or kept as previous-session evidence, and the frontend does not silently replay uncertain injections or commands.”

**If asked for depth:** Request acceptance, execution and controller acknowledgment remain distinct. Capturing target IDs at submission prevents rapid inspection changes from retargeting an in-flight request.

## Challenging questions

### Q37. How accurately does FLARE detect threats?

**Say:** “We do not have one defensible overall field-accuracy number. We have controlled synthetic counter and insider-detection results, plus bounded model training and live inference checks. Those establish different things and cannot be combined into a deployed BiLSTM accuracy claim.”

**If asked for depth:** Insider binary detection recorded TP 34, TN 9, FN 2 and FP 0 across its small protocol. The 180-sample network-counter test had no errors on its constructed distributions. Neither is representative field classification accuracy.

### Q38. What evidence supports better routing?

**Say:** “Our retained v2 evaluation gave DQN higher utility than static Direct and lowest-threat routing, with fewer switches than greedy. The myopic utility baseline was close, so the evidence supports that particular comparison rather than universal superiority.”

**If asked for depth:** Mean scores were 232.32, 119.94, 209.59 and 231.79 respectively. This is dimensionless utility from ten paired synthetic episodes. Use the topology benchmark separately for modeled packet delivery results.

### Q39. Does zero unsafe forwarding mean the system is guaranteed safe?

**Say:** “No. It means no unsafe forwards occurred under the controlled evaluation’s definition after applying the gate. The raw policy still proposed unsafe actions. Real safety depends on valid measurements, suitable thresholds and actual enforcement.”

**If asked for depth:** Current v3 results show raw unsafe-forward rate 26.91% and gated 0%. A deterministic constraint can satisfy its own modeled rule without proving hardware safety or resilience to every attack.

### Q40. How effective is poisoning protection, including false positives?

**Say:** “In the small three-seed synthetic aggregation study, protection retained mean accuracy near 95.93%, compared with 8.80% for attacked averaging. All nine malicious updates were rejected, but four of thirty-six benign updates were also flagged. No benign updates were rejected.”

**If asked for depth:** Pooled detection precision was 69.23% and recall 100%. These are logistic-model study results, not BiLSTM attack detection. This distinction prevents an inflated ‘perfect detector’ claim.

### Q41. Could a subtle or colluding attacker bypass trust checks?

**Say:** “Yes. The tests do not establish protection against every small, coordinated or backdoor update. Historical consistency and robust comparisons help, but they are not a mathematical guarantee that every malicious update becomes visible.”

**If asked for depth:** We need adaptive adversary studies, stronger non-IID baselines and explicit attack budgets. Do not claim every tiny perturbation is bounded harmlessly or that trust must eventually expose it without proving those assumptions.

### Q42. Can this run in real time with 100 drones?

**Say:** “The recorded local renderer met the FPS target, but the dense 100-UAV simulator clock lagged wall time. Smooth drawing does not prove real-time simulation execution. We should report both.”

**If asked for depth:** In separate 60-second profiles, 2D averaged 58.47 FPS and 3D 56.32 FPS, while the simulation advanced only 19 and 21 seconds. These are Apple M2/Chrome 155 local measurements, not portable guarantees.

### Q43. Which features are incomplete or unavailable?

**Say:** “Physical radio and real OpenFlow validation remain unverified here. Some advanced asynchronous federation and adaptive selection settings have live integration gaps. The latest full browser run also retained two failures. We report those separately from the passed panel rehearsal.”

**If asked for depth:** A saved setting alone is not evidence of runtime effect. Missing services and retained results must remain labeled. The exhaustive application audit must not be described as fully passing.

### Q44. What was your individual contribution?

**Say:** Choose the complete statement in [Your contribution](CONTRIBUTION.md) that matches your actual role: Mission Workspace, simulator/networking, detector/data, federated learning, routing/SDN, or integration/testing. Each option names the corresponding implemented responsibilities without assigning ownership to you. Add a concrete example only from work you performed and credit teammates accurately.

**If asked for depth:** Show one relevant source file, design decision and test or artifact you can personally explain. Repository authorship alone does not establish who designed or understood a module. This preparation cannot infer your contribution from the app’s capabilities. Use only the role statement that is true rather than claiming the whole project yourself.

### Q45. What would you improve next, and could this reach real drones?

**Say:** “First I would collect representative synchronized link measurements and calibrate the detector. Then I would verify actual packet enforcement and wireless behavior, followed by a controlled hardware integration. I would also finish the advanced federation paths and repeat the experiments with stronger baselines.”

**If asked for depth:** Autopilot/sensor adapters, compute limits, authorization, radio calibration and field safety need separate work. Current custom SAC is an experimental discrete training path, not proof of continuous multipath bandwidth allocation. Deployment needs more than replacing software UAV glyphs with physical drones.

## Answers when a demo behaves unexpectedly

- **No reroute:** “The system already uses an alternative in this run. I will show the actual installed state instead of claiming a transition.”
- **No detector verdict:** “The injection is confirmed, but this observation has not verified the expected detection. I’ll separate those facts.”
- **Threat remains after expiry:** “The condition ended. Recent observations and history remain.”
- **Packet dropped:** “This is a recorded failure with a reason. It is evidence to investigate, not something to hide with an animation.”
- **Service unavailable:** “I cannot claim a live result from that service. Here is the earlier captured evidence, labeled with its configuration.”
- **Question outside verified evidence:** “That is a reasonable next experiment. We have not established it with the current results.”

## Source anchors

Architecture and behavior: `simulation/swarm/engine.py`, `network.py`, `topology.py`, `experiments.py`, `fl/model.py`, `fl/trust.py`, `fl/aggregator.py`, `rl/agent.py`, `rl/safety.py`, `orchestrator/loop.py`, `sdn/mock_sdn.py`, `api/mission.py` and the Mission Workspace components. Dataset boundaries: `datasets/README.md`, `datasets/preprocess.py`, `fl/data.py`, `rl/traces.py`. Numerical claims: the local JSON files linked in [RESULTS.md](RESULTS.md).
