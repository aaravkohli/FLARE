# Perspective environment refinement (current renderer)

The existing adapter, fixed-step patrol dynamics, orbit model and network contracts
remain unchanged. This pass changes presentation only; earlier sections below are
implementation history where they describe an orthographic camera or finite plane.

- Perspective FOV 44°, Tactical elevation 38°, Overview 64°, restrained Follow.
  Render-space distance is derived from the clamped view span and viewport aspect.
  Overview targets the whole operation; Follow intentionally prioritizes its UAV.
- A 14km illustrative terrain plane extends beyond the 4.5km camera clip distance.
  Fog reaches the background before clipping; restrained grid and corner markers
  replace the tabletop perimeter. This does not enlarge the physical patrol bounds.
- Shared quadrotor bodies, four arms, instanced rotor discs/blades and nose cues use
  the existing heading/pitch/roll. Rotor motion uses the pausable visual clock.
  Shadows use one procedural radial texture; only selected UAVs have altitude stems.
- Ground control has a fixed pad, operations structure, mast and dish.
  The satellite remains the same 550km circular orbit at 1×. Only render mapping
  changes: 330–370 altitude units, behind the fleet, with a smaller SAT-01 model.
- Compact D identifiers use collision-aware HTML label placement. Selection and
  hover take priority. Names and installed routes remain runtime identity/evidence.
  Selecting opens the inspector; empty terrain dismisses it without clearing the
  authoritative selected UAV or changing motion.
- Dynamic curves use more visible spatial lift. Inactive links are subdued; mesh
  remains schematic with no fabricated forwarding hops. Packet timing/loss semantics
  are unchanged. Simulation scope is a compact footer disclosure; 2D is a main control.
- No dependencies or external models were added. Rotor instances and the shadow
  texture join existing disposal paths. DPR remains capped at 1.5.

Tests cover perspective clipping/framing, compact IDs and label collisions in
addition to motion, orbit, network, threats and decision evidence. Terrain, UAV
motion, compressed orbit, shadows and RF volumes remain visual simulations.
No real RF reachability, observed flight position, orbital coverage or traffic
density may be inferred from this renderer.

---

# Decision evidence presentation

Enabled by the existing mission view; no new flag, dependency or backend change.
Adapter → pure decisionPresentation reducer → HTML DecisionPanel / event timeline.
Inputs are completed canonical events (or timestamped legacy summaries), reported
source, threat, policy/request/installation, safety, containment and SDN results.
No evaluating/stabilizing phase or inference duration is invented. Scenario
injection is distinct from a detector finding. Unknown values remain unavailable.
Recovery requires reported low threat, safe installed route and valid low loss;
scenario clearing alone cannot trigger it. Event identity deduplicates snapshots,
rejects older timestamps and resets history when selected UAV changes.
HOLD removes packets immediately and desaturates world links; route opacity easing
never delays authoritative installation. Failed apply retains known installed state.

Eight focused decision tests cover missing evidence, scenario/detection distinction,
pending/failed/applied changes, rapid events, recovery, selection, HOLD/containment
and safety override provenance. Combined suite: 56 passing. Evidence is controlled
synthetic telemetry and Mock SDN; this does not demonstrate physical enforcement.
The configured Python suite excludes tests/test_adversarial.py.

---

# Authoritative 3D threat visuals

`threatState.ts` maps active profile, selected UAV identity, `jammed_paths` and
valid reported `gps.drift_m` into descriptions and visual parameters.
`ThreatLayer.ts` owns reusable Three geometry/materials and decay. It never
writes flight dynamics, network metrics, route state or attack controls.

- Spot/barrage use targeted schematic volumes (55/190 visual-unit radii), not
  measured source locations or RF coverage. Sweep rotates a schematic sector.
- Smart/reactive/adaptive/spoofing mark only reported affected links. Unknown
  mesh forwarding hops remain HTML context; no invented chain is highlighted.
- GPS spoofing uses a translucent octahedral reported-offset marker, a connector,
  and explicit HTML labelling. It is not a second UAV. Offset magnitude is capped
  at 120 visual units; direction is schematic. No trusted georeference exists.
  Missing/non-finite/nonpositive drift produces text only, not a guessed position.
- Replay uses paired repeated-telemetry marks. The generator refreshes snapshot
  timestamps while replaying RF metrics; no stale flight position/age is claimed.
- DoS clusters the existing bounded packet instances with a continuous monotonic
  curve parameter mapping. It does not fabricate traffic volume or packet rate.
  HOLD still suppresses all forwarding. FHSS shifts muted link ticks to depict
  simulated mitigation, not measured channel/frequency assignments.
- A fresh `none` creates no effect. Existing effects fade over about 0.5 visual
  seconds; state-derived network degradation remains independent. Owner changes
  discard old effects. Pause freezes the visual clock; reduced motion stops sweep
  and hopping and clears residual effects immediately.
- Persistent HTML describes profile and affected links. Threat details are
  keyboard accessible. Visual source picking never blocks UAV/asset selection.

Validation: 48 frontend tests include all 12 profiles and a real Three scene
geometry/disposal test, null/invalid GPS evidence, owner mismatch, decay, pause,
HOLD and bounded DoS progress. Typecheck, lint and production build pass; the
existing lazy-chunk size advisory remains. This is controlled visual simulation,
not field-validated RF/GPS evidence. API and attack-control semantics unchanged.

---

# Dynamic network layer

The enabled Three.js scene now consumes `networkState.ts`, `networkGeometry.ts`
and `PacketStream.ts`. `adaptMissionSimulationState` preserves explicit canonical
`installed_path` including null; only legacy summaries with successful SDN apply
can supply a fallback installed route. Requested/selected summaries alone never
activate forwarding. Existing network/API/FL/DQN/SDN logic is unchanged.

Direct and Satellite use continuously updated lifted curves. Mesh has no runtime
UAV-hop identity in the current schema: all supplied peers form a dashed schematic
neighborhood, explicitly labelled as unmeasured, with no animated forwarding hop.
This does not infer RF reachability from distance. The SVG fallback also suppresses
its old fabricated mesh chain. Logical Topology remains separate.

A maximum of eight reusable instances per physical route depict packets. Valid
reported latency controls illustrative travel duration: 0.6 seconds + 12× latency
in seconds. This visibility scaling is disclosed and never changes runtime timing.
Phase integrates smoothly when latency changes. Valid reported loss drives stable
hash-based illustrative failures; missing/invalid loss causes no invented failures.
Missing/invalid latency suppresses the stream. Density is fixed and explicitly
not measured congestion or traffic volume. No React component is spawned per packet.

Only confirmed installed state activates streams; unknown or mismatched selected
UAV state emits none. HOLD emits none immediately. Mesh activation remains logical.
Old/new line opacity may ease, but packets migrate in the same frame as runtime
installation. Failed apply may retain the authoritative previously installed path.
SDN-installed timeline events and route notices now require a known installed path
and successful runtime apply; no local timer emits installation events.

Observed validation: all 32 frontend tests, typecheck, lint and production build
passed, and the full configured Python suite passed 258 tests (two existing
warnings). Browser spot jam caused authoritative Direct → Satellite → Direct;
barrage caused HOLD with zero packet instances, then Direct recovery. Selection
clears forwarding until the selected UAV's installation is known. Console clean.
No real-network/packet capture validation or authoritative mesh-hop trace exists.
The existing lazy-chunk size warning remains. Original Spot/15s controls restored.

Six new tests cover null installation, pending/failed/installed/HOLD/recovery states,
selected-UAV mismatch, mesh honesty, warning flags, moving endpoints, latency,
phase continuity, pause and deterministic loss. All evidence is controlled visual
simulation and local synthetic/Mock-SDN integration, not real packet forwarding.

---

# 3D environment — current enabled renderer

The default mission view now lazy-loads `MissionWorldCanvas → createMissionScene`
using Three.js. The preceding motion/orbit implementation notes below describe
the retained SVG renderer and unchanged pure simulation modules.

- `cameraRig.ts`: pure bounded orthographic camera presets, damping and eye position.
  Tactical defaults to 55° elevation. Overview fits the mission; Follow tracks the
  selected visual position with a fixed restrained offset. Rotation/zoom controls
  never write to the world. Manual panning is intentionally unavailable.
- `terrain.ts`: static low-detail illustrative relief below the 35 m UAV floor;
  no surveyed terrain, geographic location, collision model or RF propagation.
- `satelliteWorldPosition.ts`: normalized orbital direction maps into x ±240,
  y 250–290 and z −220…−140 render units. Literal orbital metres never reach cameras.
- HTML labels, camera buttons, fleet selection, asset details and telemetry remain
  accessible outside WebGL. Crowded visual labels are suppressed, but every fleet
  selection button remains available. Routes remain schematic; mesh relay identity
  is not supplied by the backend. An amber ring marks an injected event, not its
  measured spatial extent. Network HOLD hides packet glyphs without stopping flight.
- One mounted renderer owns RAF and the existing shared clock. Pause/reduced motion
  freeze flight, orbit and packet time. Hidden tabs cancel scheduling; resume resets
  the wall-time origin. Reduced motion makes camera commands immediate.
- Shared fleet geometry/materials, DPR ≤1.5, static terrain normals, inexpensive
  ground discs, no real-time shadows or postprocessing. Scene resources and event
  listeners are disposed on unmount. Frozen scenes render on updates/resize only,
  except an explicit camera command can finish its transition.
- `VITE_MISSION_RENDERER=svg` forces the retained renderer. “Use 2D view” provides
  an operator fallback; “Try 3D view” retries. WebGL initialization/context loss
  and lazy-component errors fall back without discarding the shared world clock.
- At ≤1100px telemetry moves below the scene; larger widths reserve a separate
  240px telemetry column. Canvas framing adapts to aspect ratio and reserves sky.

No backend/API/schema/checkpoint dependencies changed. All coordinates are visual
simulation, not measured flight, coverage, network evidence or flight control.
Dependencies added: Three.js and its development TypeScript declarations.

Validation commands: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`.
The five camera/environment tests supplement the existing 21 motion/orbit tests.
Browser acceptance covers 1440/1280/1024/768px, all camera controls, fleet selection,
asset inspection, pause/resume, topology and renderer switching. Observed on 2026-09-16: 26 frontend tests, typecheck, lint and build passed;
258 Python tests passed with two existing dependency deprecation warnings.
The configured Python suite excludes `tests/test_adversarial.py`.
Browser review covered all four requested widths, direct mesh picking, keyboard
selection, follow/reset/overview, asset details, exact paused label positions,
resume and topology/SVG/3D switching. Console errors/warnings were absent on the
clean run. The five-drone scene reported 59 draw calls. The initial wide-view
satellite clipping was corrected by fitting a minimum vertical field.
The lazy Three.js scene chunk is about 139 kB gzipped and triggers Vite's 500 kB
uncompressed chunk advisory. npm installation reported two dependency advisories;
no broad dependency upgrades were attempted. Device-level GPU utilization,
OS reduced-motion emulation and WebGL driver failure injection remain unrun;
reduced-motion camera behavior has pure-test coverage.

---

# Mission motion layer

This is a deterministic **visual simulation**, not flight control, measured UAV
motion, an RF simulator, or a collision-avoidance autopilot.

## Runtime and ownership

`App → LiveMissionSimulation → AirspaceCanvas → useSvgWorld → frameClock → stepWorld`

The enabled mission view projects a 3D local world onto the retained SVG renderer.
`LiveMissionSimulation` owns one transient clock ref for the mounted mission.
React owns network state, accessible controls and elements; the renderer bridge
owns frame-level SVG transforms, shadows, patrol geometry and route endpoints.
There are no per-frame React state updates and no new runtime dependencies.

The existing adapter, backend API, WebSocket, FL/standard-DQN/SDN behavior and
checkpoints are unchanged. Dynamics accepts only fleet IDs and elapsed visual
time. Selection changes the annotation/patrol highlight, never the trajectory.
HOLD suspends network packet depiction through the existing renderer condition;
it does not stop simulated flight. Attacks do not affect world motion or create
RF values. GPS telemetry is not used as trusted motion input.

## Pure modules

- `dynamics.ts`: metre-based vectors; Y-up world; deterministic ID hash; 12-point
  patrols; current position/velocity/acceleration and heading/pitch/roll; waypoint,
  target, mode and per-aircraft limits. Fleet reconciliation preserves existing
  aircraft and sorts IDs for deterministic avoidance accumulation. A spatial
  hash limits neighbor queries.
- `clock.ts`: 60 Hz fixed step, accumulator, maximum 100 ms of elapsed work per
  frame, explicit pause/visibility reset, no hidden-tab catch-up. Its independent
  `simulationSeconds` retains fractional elapsed time across resets for orbital
  evaluation; it advances at 1× accepted foreground time even with no UAVs.
- `satelliteOrbit.ts`: pure analytic circular orbit in Earth-centred inertial SI
  coordinates; no dependence on UAV dynamics, renderer, selection or telemetry.
- `satelliteRender.ts`: maps orbital direction into a bounded, compressed sky
  band. Only these render coordinates reach the SVG satellite and link endpoints.
- `projection.ts`: orthographic oblique projection and render-only interpolation.
  A future Three/R3F renderer can consume the same world without changing its
  dynamics or backend contracts.
- `useSvgWorld.ts`: DOM-ref bridge; RAF lifecycle, reduced motion and visibility
  handling. No scheduled RAF while frozen. Layout updates reconcile fleet and
  refresh element references after React changes, including new packet elements.

Units: 720 × 480 m horizontal volume, altitude 35–130 m. Initial patrol altitudes
are approximately 48–111 m; speed limits are 14–17 m/s, acceleration 4 m/s²,
turn rate 0.55 rad/s, climb/descent 2 m/s, bank limited to 0.24 rad. These are
chosen simulation settings, not measured vehicle specifications.

Waypoint lookahead starts turns before corners. Velocity changes share a 3D
acceleration/speed budget. Roll follows turn rate with exponential damping.
Predictive separation blends steering, altitude deconfliction and braking;
the boundary return zone anticipates braking distance. Positions are integrated,
never clamped or wrapped. Invalid externally supplied initial motion states are
not supported: an aircraft already outside the volume, or unable to stop before
impact, cannot be made safe without violating those physical constraints.

## Preserved renderer and limitations

The SVG remains the renderer for this first layer; no complete terrain/camera/
Three.js redesign is claimed. Base, jammer and mesh routing graphics are
schematic; satellite motion now follows the circular model described below.
Mesh relay identity is not supplied by the backend. With one
aircraft the mesh route has no invented second aircraft. Icon sizes are for
legibility, not physical scale; projected glyphs can overlap at different depths.

Pause Visuals and reduced motion freeze the world. Logical topology temporarily
unmounts the airspace, retaining world state and clearing wall-time debt. Returning
to mission view continues from the retained world. Hiding a browser tab also
resets the time origin. A reload starts the same world for the same fleet.

Every supplied unique fleet ID is simulated and rendered; there is no five-drone
cap. Finite-volume density still matters: spawn deconfliction tries 256 stable
phases, then retains the last candidate. Avoidance is validated for the tested
encounters/densities, not guaranteed for arbitrarily crowded fleets. Crowding
reduces patrol speed. No position correction or teleport hides congestion.

Existing frontend provenance issues remain outside this motion change: cached
telemetry typing, stale summaries during drone switching/unavailable telemetry,
requested-versus-installed copy and local timeline wording need a separate pass.

## Satellite orbit and render compression

`DEFAULT_SATELLITE_CONFIG` is the single configuration point for the enabled
orbit: Earth mean radius 6,371 km, GM 3.986004418e14 m³/s², illustrative altitude
550 km, inclination 53°, initial phase 60°, simulation epoch 0 seconds. Constants
are based on the [NASA Earth Fact Sheet](https://nssdc.gsfc.nasa.gov/planetary/factsheet/earthfact.html)
and [NGA WGS 84](https://earth-info.nga.mil/?action=wgs84&dir=wgs84).
This is a simulated object, not a tracked real satellite or real ephemeris.

The pure model computes `r = EarthRadius + altitude`, `ω = sqrt(GM / r³)` and
`θ = θ₀ + ω(t − epoch)`. It rotates the circular plane by the inclination and
returns position in metres, tangential velocity in m/s and dimensionless
geocentric direction. The default period is approximately 95.5 minutes. Phase
is reduced modulo the period before trig evaluation to avoid accumulating
integration error and huge trig arguments. Invalid input/configuration throws
`RangeError`; nothing substitutes fabricated coordinates.

The rendering module maps normalized direction into x=465…775 and y=42…66 SVG
units. It does not pass metre-scale orbital positions into the UAV volume or
any camera target/bounds. This deliberately always-visible orbital band is
**not observer-relative azimuth/elevation, horizon visibility or coverage**;
Earth rotation, perturbations, eclipse, occlusion and line of sight are outside
this approximation. The map stays above the normal patrol layer. Satellite
geometry and both legs of its communication path use the same point each frame.
This changes link geometry only, never route availability, selection or latency.

Time is 1×; there is no acceleration control or hidden scale multiplier. The
caption and satellite details show 1×, and details show altitude, inclination,
period and render compression alongside the unchanged backend link latency.
The label denotes running visual time: pause, hidden-tab/reduced-motion handling,
topology view and large-frame clamping follow the mission clock's existing
suspension policy. Resume establishes a new wall-time origin without skipping
forward in the orbit. Ten seconds of active normal rendering moves the default
satellite roughly 1.5 SVG units while UAVs visibly traverse their patrols.

## Validation

Use a Node version with TypeScript stripping (the development environment used
Node 25.2.1; the test command enables the stripping flag explicitly). No test-only
browser backend or fabricated live telemetry is installed.

```sh
cd frontend-react
npm test
npm run typecheck
npm run lint
npm run build
```

Tests cover deterministic initialization/reordering, 32-aircraft spawn spacing,
50-aircraft reconciliation, pure input preservation, ten-minute 16-aircraft
limits/progress, bank damping, boundary recovery, vertical braking, head-on and
dense patrol separation, different frame partitions, paused/hidden/large deltas,
and interpolation across the heading wrap. Browser checks cover selected-drone
keyboard interaction, route inspection, topology, pause/packets, panel controls
and clean-load console errors against the existing local synthetic/Mock-SDN app.

Observed validation on 2026-09-16: all 12 motion tests passed, as did typecheck,
lint and the production build. `venv/bin/python -m pytest -q` passed 258 tests
with two existing dependency deprecation warnings. The configured Python suite
excludes `tests/test_adversarial.py`; no real-network or hardware run was made.
A five-second barrage issued through the existing browser control produced
backend HOLD with zero packet glyphs; automatic recovery restored Direct and
packet animation. The original profile/duration/selection were restored. Final
clean-load browser logs contained no warnings or errors. A development hot
reload after changing hook structure required a page reload; it did not recur
on clean load or subsequent interactions.

Next migration: protect/fix the existing provenance presentation, extract a
renderer interface, and add a lazy-loaded Three/R3F world with an SVG fallback.
Keep the same clock/dynamics and HTML accessibility controls. Delete neither
SVG nor logical topology until feature parity is independently demonstrated.

Satellite validation adds nine tests (21 total): configuration/epoch determinism,
period and circular geometry, 1× progression across frame partitions, exact
fractional-time pause/resume, independence from fleet changes, compressed mapping,
live link endpoint geometry, long-period finite states and invalid-input rejection.

## Final UX and regression pass

Mission-only refinements: World/Network labels, larger camera targets and world
labels, consistent focus outlines, full timeline text, simulation-scope disclosure,
and manual-camera preset indication. Threat picks dispatch to asset details;
unknown picks cannot reach drone selection. Moving UAVs have an 18px screen-space
picking tolerance. Existing aircraft, satellite, terrain and backend contracts remain.

Transient curve sampling reuses storage, fleet reconciliation runs on fleet-prop
changes, and the previous-frame lookup map is reused. Fleet-sized mesh buffers are
disposed before replacement. RAF, resize, pointer, visibility and media-query
listeners are cleaned up; materials, geometry, instances and renderer are disposed.
This is not an allocation-free loop: immutable dynamics, interpolation and label
layout still allocate. DPR is capped at 1.5, with no shadows or post-processing.

Validation: 58 frontend tests; typecheck, lint and production build; 258 configured
Python tests (two deprecation warnings). Five-drone browser scene: 59 draw calls,
23 geometries, one renderer-managed texture. These are scene counters, not GPU-time/heap profiling.
The lazy Three chunk still exceeds Vite's 500KB advisory. Browser interactions
and screenshots were checked at 1440, 1280, 1024 and 768px. Reduced-motion logic is
covered by source review and camera tests; OS/screen-reader testing remains manual.
