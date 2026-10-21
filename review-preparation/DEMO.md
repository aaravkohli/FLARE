# FLARE live demo: exact actions and speaking guide

Use the six-minute route for the review. The extended route adds investigation and experiments. Both use real frontend requests against the software simulator. Neither establishes physical UAV or RF performance.

The core route was executed again on 21 October 2026 against a fresh production frontend and isolated backend. See [the evidence manifest](evidence/manifest.json). Outcomes belong to those runs, not a promise that every future run will produce the same detector verdict.

## Before the audience arrives

Keep the existing operator mission running if it contains work you need. A restart is not a harmless readiness check. The normal `run_local_simulation.sh` launcher restarts its owned services, bootstraps a mission registry, starts training and can change local runtime state.

For a disposable review session, run this from the repository root in a new terminal:

```bash
venv/bin/python -m scripts.swarm_test_services --production \
  --simulation-port 18480 --api-port 18400 --frontend-port 15473
```

It creates a new temporary source/configuration/model/runtime copy, builds the current frontend and prints the artifact directory. Leave that terminal running. Open **http://127.0.0.1:15473/#mission**. Stop this disposable stack with Ctrl+C in its own terminal afterward. If a port is occupied, use another unused set and open the frontend port it prints. Do not kill unrelated listeners.

The core demo needs the API, modeled SDN/simulator, orchestrator, inference checkpoints and frontend. It does **not** require a running Flower training round. The FL Update Gate operates on copied model parameters. Actual live training is a separate optional demonstration.

Sign in with the configured operator account. The repository's development defaults are `admin` / `antijam2026`; use your configured credentials if changed. These defaults are for local development, not a production security claim.

Prepare this disposable mission while the audience is not watching:

1. Open **Mission**, then **Live mission**.
2. Click **Pause mission** if it is running.
3. Open **Mission setup** and set **UAVs / Fleet size: 10**, **Formation: Circle**, **Scenario: Normal**, and **Radio range: 1000 m**. Close the drawer after each change if necessary and wait for the toolbar to become enabled. Configuration applies immediately. Scenario selection resets the mission.
4. In the main toolbar set **Topology: Ring** and **Speed: 1×**.
5. Open **Mission setup**, click **Reset**, then close it. This intentionally starts a new disposable session with the chosen formation. Do not reset the operator's working mission.
6. Select **2D network**. Use **Camera → Fit fleet** and close Camera.
7. Open **Inspect** and choose **UAV-001** in **Inspected UAV**. Its internal identity is `drone_1`.
8. Open **Events**. Set **Condition group: Communication**, **Profile: Spot jam**, **UAV scope: Inspected UAV**, **Communication target: Direct**, **Injection duration: 15**. Do not inject yet.
9. Open **View settings → Service readiness**. Confirm the orchestrator reports the detector loaded and the expected DQN source once inference has run. A connected page alone does not prove models loaded. At a newly reset paused T+0 session, awaiting telemetry is legitimate.
10. Keep [the presentation](presentation/output/FLARE-panel-review.pptx), [results](RESULTS.md) and saved screenshots available offline. Finish with menus closed and the mission paused.

Circle and 1000 m are deliberate demo settings, not the scientific benchmark defaults. They make the small Ring route easy to inspect. The benchmark uses its own independent configuration.

## Core demonstration: approximately 5–7 minutes

### 1. Establish the meaning of the canvas — 0:00–0:45

**Click:** Global **Mission**, workspace **Live mission**. Keep **2D network** visible. If needed, choose **Inspect → Inspected UAV → UAV-001**, then return to **Events**.

**Say before interacting:**

> “This is our software packet simulator. Python owns the positions, links, time and packet outcomes. The canvas displays that state. These are ten UAVs in a circle, with Ring connection rules.”

**Point out:** Ten active UAVs and a closed ring of eligible UAV links. Missing delivery or latency at T+0 appears as “—” because no measured delivery exists yet. Ground-control links and an installed telemetry path are separate from the UAV topology.

**Behind the screen / why it matters:** Physical radio eligibility limits the topology graph. A line shows connectivity or forwarding policy, rather than successful packet delivery.

**Transition:** “Let’s send a packet through several drones and inspect what actually happened.”

### 2. Demonstrate a five-hop peer packet — 0:45–2:00

**Click:** **Route probes** in the canvas toolbar.

- **Source: UAV-001** (`drone_1`).
- **Destination: UAV-006** (`sim_uav_006`).
- Enable **Highlight probe route**.

**Say:**

> “This is a candidate peer route. It is separate from the selected UAV’s telemetry route to ground control. I can inspect the proposal without changing command targets.”

**Observe:** The summary should show five hops under this prepared configuration. Read the actual displayed sequence. In the recorded run it was UAV-001, 002, 003, 004, 005, 006. Ground control is not an intermediate relay.

**Click:** **Send probe**, close Route probes, then **Play mission**. Let the clock advance about eight simulation seconds and click **Pause mission**. Reopen **Route probes** with the same endpoints.

**Say while running:**

> “The backend schedules the packet’s individual transmissions, queue waits and arrival. A moving marker is only a view of those events. The recorded result is the delivery evidence.”

**Observe:** Under **Recent probe outcomes**, show the actual `delivered`, `dropped` or other recorded result. In the verified rehearsal, a 512-byte probe delivered across five hops in **48.16 ms of simulation time**, with no retries. That latency comes from the event trace, not a stopwatch or animation duration.

**If the run differs:** Show its real outcome and reason. Do not announce the saved 48.16 ms as this run’s value. If no route is reachable, recheck endpoints, prepared formation/range, controller/containment state and session. Do not repeatedly click Send to hide a failure.

**Transition:** “We can now disrupt a communication option and inspect the response independently.” Close the popover.

### 3. Inject a bounded Direct condition — 2:00–3:20

**Click:** Main **Topology → Full Mesh**. This changes connection rules while retaining current positions. Keep the mission paused. Open **Telemetry** and note which family is currently **Active**; then open **Events**.

**Inputs:** **Communication**, **Spot jam**, **Inspected UAV**, **Direct**, **15** simulation seconds. Verify **Targets: UAV-001** before submitting.

**Say before clicking:**

> “I’m injecting a fifteen-second modeled condition on this UAV’s Direct communication option. Injection changes the channel condition. It does not write an ‘attack detected’ verdict into the model.”

**Click:** **Inject spot jam**, once.

**Observe:** An authoritative active condition with captured targets and a remaining-time display. While paused, it should stay at the same remaining simulation time and show **Paused** / the paused-timer note. The **Injection duration** input still configures the next injection.

**Click:** **Play mission**. Show the countdown decreasing with the mission clock. Open **Telemetry**, select **Direct**, then open **Decisions**.

**Say while running:**

> “Here I separate measured impairment, the detector’s observation, DQN’s proposed communication family, the safety check and the controller’s confirmation. Each is a different fact.”

**Observe, without promising a verdict:** Actual Direct measurements, detector source and scores, policy/request, safety reason, and installed family. The first rehearsal reported `fl_model` and `rl_model` with the v3 routing contract. Satellite was already installed before the jam and remained installed during it. The network-security analyzer still reported NORMAL in that capture. This is a valid distinction between RF risk and traffic misconduct.

**Exact line if Satellite was already active:**

> “The system already uses Satellite, so this run demonstrates continued use of that alternative. I am not claiming a route transition that did not occur.”

**Behind the screen / why it matters:** The DQN chooses a family, the network engine computes permitted hops, and the SDN adapter confirms logical forwarding. Local confirmation is simulated enforcement, not real OpenFlow packet proof.

### 4. Show automatic expiry and retained evidence — 3:20–4:10

**Click:** Return to **Events**. Let the mission reach the condition’s expiry time. Do not manually clear it first.

**Observe:** The latest receipt becomes **Expired**, includes a simulation timestamp and says **Condition ended automatically**. Active conditions becomes empty. Then pause the mission.

**Say:**

> “The condition ends on the simulation clock. We retain detector history, rolling packet measurements and containment evidence. That is why expiry does not promise an immediate zero threat score or an immediate return to Direct.”

**If there is a stale connection:** The display freezes and labels stale evidence. Wait for authoritative state rather than counting wall seconds or resubmitting an uncertain request.

**Transition:** “The second risk is a participant corrupting the shared model rather than its radio link.”

### 5. Demonstrate the copied-model update gate — 4:10–5:30

**Click:** Global **Validation → FL Update Gate → Start isolated test**. If a result already exists, the action is **Run test again**.

**Say before clicking:**

> “This test compares a clean control, ordinary aggregation with a harmful update, and protected aggregation. It uses in-memory copies and leaves the deployed model unchanged.”

**Observe:** Wait for the actual completed result. In the verified run, all five protection checks passed: hostile candidate rejected, candidate weight removed, protected aggregate closer to the clean control, deployed model untouched, trust/client state untouched.

**Say while it runs / after completion:**

> “The server examines update consistency and trust before allowing influence. This demonstrates one deliberately constructed harmful update. The distance and damage-reduction values describe this copied-model test. They are not the detector’s classification accuracy.”

**If it fails:** Read the failed check or error. Do not call the result a pass because most rows are green. Use the saved successful screenshot only as a clearly labeled previous rehearsal.

### 6. Close the demonstration — 5:30–6:00

**Say:**

> “We have shown an actual modeled packet outcome, a condition with authoritative expiry, separate decision and enforcement evidence, and an isolated update-protection check. FLARE gives us an inspectable research workflow. Real wireless and physical drone validation remain future work.”

Return to **Mission** if asked. Global navigation preserves the live mission rather than creating a new one. Inspection and command targets remain independent.

## Extended demonstration: approximately 10–15 minutes

Keep the six-minute route and add the following in this order. Skip an optional segment if the review time is short. The smaller experiment below is a demonstration configuration, not the saved 30-run scientific benchmark.

### A. Packet replay and 3D parity — add 1 minute

After Step 2, open **View settings → Packet replay**. Select the delivered probe in **Recorded packet**, click **Replay 0.1×**, and point to timestamps and its recorded route. Restore **Live events** afterward. This replay clock is distinct from the mission clock and attack timer.

Switch to **3D world**, then **Camera → Fit fleet**. In **Layers**, set **Connection emphasis → Strong** if lines need more contrast. Use **Selected installed route**, **Ground-control links**, **Color links by RF quality** and the legend to explain extra colored connections. Close the menu and return to **2D network**.

> “Both views use the same packet evidence. Cyan UAV topology links, an installed forwarding overlay and a dashed probe candidate represent different relationships. Terrain facilities are illustrative scenery.”

### B. A queued command and independent selection — add 1–2 minutes

After the attack expires, pause Live Mission. Open **Commands**, click **Clear**, check **Target UAV-001**, choose **Command → Altitude**, enter **120**, and click **Send to 1 UAV** once.

Show the paused queue note. The accepted command has a command ID and per-UAV queued outcome. Click **Play mission** and open bottom **Activity**. Show the actual received/executed/acknowledged stages or failure. An acknowledgment does not mean the UAV has already reached its altitude.

> “Inspecting a UAV chooses the evidence I read. Target checkboxes choose who receives a command. The paused command waits for simulated delivery, and an acknowledgment is distinct from acceptance.”

Pause after observing the result. Flight commands have a ten-second simulation TTL in this UI. Controller loss, radio failure or containment may prevent acknowledgment. Do not resend uncertain commands automatically.

### C. Synchronized A/B topology comparison — add 2 minutes

Open **A/B comparison**. Set **Topology A: Full Mesh**, **Topology B: Ring**. Open **Shared settings** and choose **UAVs: 10**, **Scenario: Normal**, **Range: 350**, **Seed: 42**, **Duration: 10**. Close settings and click **Create comparison**.

Click **Play comparison**, let both clocks advance, then **Pause comparison**. Pan/zoom and change **Inspect UAV** to demonstrate synchronized views. Show aligned **Live graph statistics**, which differ from final packet-cohort measurements.

> “The runs share geometry, movement, offered traffic, radio configuration and schedule. They use a fixed trust-aware mesh reference policy. Live DQN and satellite transport do not participate in this comparison.”

Choose **Cancel experiment** when finished. It retains available partial results and does not reset the live fleet. To show final B − A measurements, rehearse a completed short run in advance and label its configuration. Do not wait for a long default run during a short review.

### D. All-ten benchmark and exports — add 2 minutes

Open **Topology benchmark**. For a quick demonstration choose **UAVs: 10**, **Range: 350**, **Scenario: Normal**, **Seeds: 7**, **Duration: 10**, then **Run all ten**. Show progress and partial/final labeling. It may take longer than the panel allows, so keep the saved results ready.

After completion: choose **Compare metric → Latency**, toggle **Show seed 7**, sort the table by **Latency**, and expand a **Details** row. Show packet conservation and any unrecovered flows. Restore the seed filter, then choose **Export JSON** or **Export CSV**. Filters change the view, not the complete export. Open **Methodology** and explain approximation limits.

For the actual scientific discussion, use [RESULTS.md](RESULTS.md): 30 UAVs, three seeds 7/42/99, ten topologies. Do not mix those values with this smaller demonstration run. The current duration implementation measures a cohort over [warmup, duration), so 5 and 60 correspond to 55 measured seconds, plus a final TTL drain.

### E. Traffic Evidence, only if rehearsed — add 1–2 minutes instead of D

Use a clean target in a running disposable mission. Global **Validation → Traffic Evidence**. Set **Target UAV** explicitly, choose **Control flood**, and click **Run test** once.

The runner first requests normal traffic and captures a measured baseline. It then requests the selected condition and waits for observed analysis and confirmed containment. Read the resulting **Normal baseline** and **Attack / protection response** columns. A timeout, missing evidence or failed enforcement is a legitimate failed run, not a predetermined green result.

**Clear traffic condition** removes the injection while retaining history and containment. This workflow changes the chosen UAV, unlike the copied-model FL Update Gate. It can consume roughly half a minute if both waits time out, so omit it from the core six-minute route.

## Failure recovery: exact, honest responses

| Symptom | Action | What to say |
|---|---|---|
| API or simulator stale | Stop interacting, read service readiness and the disposable stack’s API/simulation/orchestrator logs. Recover only that stack in a fresh terminal and a fresh temporary copy. | “Live evidence is unavailable. I’ll show the saved rehearsal while checking the service.” |
| No detector or DQN model | Confirm checkpoint/readiness and displayed source. Continue packet/topology features with fallback explicitly labeled, or use saved model evidence. | “This session reports a fallback; I won’t present it as learned-model inference.” |
| Attack says Sending or delivery uncertain | Inspect active conditions/history before any retry. Pausing freezes duration; it does not normally block the injection HTTP acknowledgment. | “I’m checking whether the request reached the engine before submitting anything else.” |
| Threat stays high after expiry | Show empty active conditions and retained timestamped evidence. Do not manually clear history to manufacture a clean score. | “The injected impairment ended; the detector still has its recent window.” |
| No route / packet dropped | Read the reason, endpoints, physical graph and containment. Keep the recorded outcome visible. | “This route is not currently feasible. The simulator records that failure.” |
| No safe route | Inspect contract and safety evidence. v3 can use Network HOLD; retained v2 can choose a least-risk degraded route. | “The behavior depends on the loaded contract and safety configuration.” |
| 3D slow or WebGL unavailable | Return to **2D network**, close extra browser tabs and use ten UAVs. | “The simpler view shows the same network state.” |
| Training metrics unavailable | Do not start a full training run on stage. Use the isolated Update Gate and saved bounded training evidence. | “No current aggregation has been reported in this session.” |
| Validation fails or times out | Show its actual error/failed check. Use a saved result clearly labeled as previous evidence. | “This execution did not verify protection; here is the earlier recorded run and its limits.” |

## Implementation anchors

The button labels and behaviors above come from `SwarmCommandCenter.tsx`, `NetworkInspector.tsx`, `MissionControlPanel.tsx`, `AttackLab.tsx`, `ExperimentPanel.tsx`, `ExperimentResults.tsx`, `application/Validation.tsx` and `application/useTrafficValidation.ts`. The network engine computes hops. The SDN adapter confirms/applies rules. The detector is never instructed to return a predetermined label by these scenario scripts.
