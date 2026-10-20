# FLARE application views

`App` owns authentication, telemetry, the WebSocket, policy drafts, and validation execution. Hash navigation only changes which application pane is visible. The Mission Workspace stays mounted at a stable position; never conditionally mount it based on the active destination or key it by destination.

Fleet and Security detail selection is local to those views. `Inspect in Mission` updates the existing inspection store and navigates to Mission. It never changes command targets. Fleet enrollment does not retarget the mission.

Policy controls reuse the existing configuration and save endpoint. Monitoring reads current-session federation evidence; missing metrics are not zero, and unavailable or mismatched snapshots cannot establish a verdict. The original policy is restored after browser tests, including failed assertions.

Traffic validation captures its UAV, scenario, and mission session at submission. It uses the existing controlled simulation endpoint, waits for real observations, and keeps incomplete observations visible without claiming enforcement. Uncertain submissions are never automatically retried. FL Update Gate retains the existing isolated copied-model test. Navigating between views does not cancel either test.

`presentation.ts` contains frontend formatting and selection helpers. Chart gaps represent absent measurements; signed attribution values remain exact. No metric formula, model, or backend contract is implemented here.

Styles in `application.css` apply to the signed-in shell and extracted views. The only Mission-specific rules size its outer frame beneath the global header. Sign-in and Mission internals retain their own styles.

Verification:

- `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`
- `FLARE_TEST_PRODUCTION=1 npm run test:browser -- --trace off`
- Add `FLARE_MESH_PROFILE=1` to include the sustained 100-UAV 2D, 3D, and A/B profiles.

Production browser tests start the existing isolated local service harness. Test data and artifacts are separate from the operator fleet; policy endpoint tests restore the existing configuration after each test.
