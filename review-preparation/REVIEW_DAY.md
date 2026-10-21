# FLARE review-day card

Keep this beside the laptop. Presentation: [ten slides with notes](presentation/output/FLARE-panel-review.pptx). Full click sequence: [DEMO.md](DEMO.md). Questions: [QUESTIONS.md](QUESTIONS.md).

## Before the panel

- [ ] Use a disposable session. Preserve the operator's running mission and checkpoints.
- [ ] Start only the review stack if needed, from the repository root:

```bash
venv/bin/python -m scripts.swarm_test_services --production \
  --simulation-port 18480 --api-port 18400 --frontend-port 15473
```

- [ ] Leave that terminal running. Open **http://127.0.0.1:15473/#mission** and sign in. Do not run a second launcher on occupied ports.
- [ ] Confirm API, simulator/controller and orchestrator readiness. In **View settings → Service readiness**, distinguish loaded models from fallback/unavailable states.
- [ ] Prepare **10 UAVs, Circle, Normal, 1000 m, Ring, 1×, 2D**, paused at a fresh disposable session. Configuration lives in Mission setup except Topology and Speed. Reset intentionally only in the disposable mission.
- [ ] Inspect **UAV-001**. Events: **Communication / Spot jam / Inspected UAV / Direct / 15 seconds**. Check target identity before submission.
- [ ] Keep one demo browser tab so its observed packet/replay history stays available. Close menus before starting.
- [ ] Keep the presentation, saved screenshots and results offline. Rehearse once with the exact display/browser you will use.
- [ ] Choose the truthful role statement in [Your contribution](CONTRIBUTION.md) and name teammates' responsibilities accurately. Do not attribute all modules to yourself by default.

## Opening, about 30 seconds

> “FLARE stands for Federated Learning and Adaptive Rerouting Engine. It is a research prototype for keeping a drone network communicating when links are disrupted or participants become untrustworthy. It connects threat observations, adaptive communication choices, safety checks and confirmed forwarding. It also protects collaborative learning from suspicious updates. Today I will demonstrate modeled packets in a software simulator. This is not a physical drone deployment.”

The two-minute explanation and five-minute presentation script are in [the main guide](README.md) and [the slide script](presentation/output/FLARE-speaker-script.md).

## Six-minute click sequence

| Time | Action | Speaking cue |
|---|---|---|
| 0:00–0:45 | Mission → Live mission, prepared Ring, 2D | “Python owns the execution state. Lines are not packet receipts.” |
| 0:45–2:00 | Route probes: UAV-001 to UAV-006, Highlight, Send probe. Play about eight simulation seconds, Pause, reopen outcomes. | “Here is the actual recorded peer-packet result.” |
| 2:00–3:20 | Full Mesh. Note active family. Events → verify prepared inputs → Inject spot jam once. Play, inspect Telemetry and Decisions. | “Injection, observation, policy and installed forwarding are separate facts.” |
| 3:20–4:10 | Let the active condition expire automatically, then Pause. | “Expiry ends the injection. It retains recent evidence and containment.” |
| 4:10–5:30 | Validation → FL Update Gate → Start isolated test | “This is a constructed update test on copies. The deployed model remains unchanged.” |
| 5:30–6:00 | Close with findings and limitations | “We demonstrated an inspectable simulated workflow. Hardware validation remains outstanding.” |

For 10–15 minutes, add Packet replay, 3D/connection emphasis, a queued altitude command, synchronized A/B, and a small all-ten benchmark as described in [the runbook](DEMO.md). Do not start a long training/convergence study on stage.

## Numbers to remember, with their labels

- **48.16 ms:** one 512-byte five-hop rehearsal probe, simulation time, no retries.
- **96.00% / 9.84 ms** versus **99.88% / 38.50 ms:** Full versus Partial Mesh, 30 UAVs, seed 42, normal scenario, 350 m, fixed mesh reference. Not a universal winner.
- **232.32 / 209.59 / 119.94 / 231.79:** retained v2 DQN / lowest-threat / static Direct / myopic utility. Ten paired synthetic episodes. Dimensionless utility, not accuracy.
- **FL Update Gate:** five checks passed in the recorded copied-model test. Its damage reduction is aggregate distance, not classifier accuracy.

Only if asked for deeper research: the small synthetic logistic-model poisoning study had protected mean accuracy 95.93% versus attacked averaging 8.80%, with 9/9 malicious updates rejected, **4/36 benign updates flagged**, and zero benign updates rejected. Do not omit the false flags or relabel it BiLSTM accuracy.

## Four distinctions that prevent difficult follow-ups

1. DQN chooses a family. The network computes hops. The controller confirms application. Packet outcomes verify delivery.
2. Flight HOLD stops movement. Network HOLD suspends data forwarding. Current v3 and retained v2 have different contracts/fallbacks.
3. RF packet loss and detector risk are different measurements. A Direct jam is not proof of malicious Mesh forwarding.
4. Topology is not formation. Ground-control links, installed routes and probe candidates are separate visual layers.

## If something goes wrong

| Situation | Response |
|---|---|
| Satellite was already active | Say so. Show continued use rather than inventing a Direct-to-Satellite transition. |
| No route or dropped probe | Read the actual reason. Check source/destination and current graph. Keep the failure visible. |
| Sending/uncertain request | Inspect active state before retrying. Do not submit repeatedly. Pausing normally freezes execution time, not the injection acknowledgment. |
| Countdown stops | Check Pause or stale connection. It follows simulation time, not wall time. |
| Risk stays high after expiry | Show empty active conditions and retained timestamped evidence. Do not claim history reset. |
| Detector/DQN unavailable | Label fallback/unavailable. Continue packet features or show a saved, clearly dated capture. |
| 3D slow or unavailable | Return to 2D. Both use the same backend state. No “locked 60 FPS” promise. |
| Validation fails/times out | Show its failed check. Use previous evidence with its label, not as this run's result. |
| Backend unavailable | Stop mutations, read readiness/logs, recover only the disposable stack. Use screenshots while checking. |

## Honest closing

> “The strongest result is that we can configure a condition, observe measurements, inspect policy and verify what was applied. Controlled tests support that workflow. Representative RF data, real packet enforcement and physical UAV tests remain necessary.”

Afterward, Ctrl+C only the review stack's terminal. Preserve exported evidence and your operator mission. Do not describe the entire application as passing: the latest complete browser suite retained failures, and real-network gates remain blocked.
