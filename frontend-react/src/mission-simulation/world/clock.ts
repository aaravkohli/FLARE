import { MAX_FRAME_DELTA, STEP, stepWorld } from './dynamics.ts';
import type { MissionWorld } from './dynamics.ts';

export interface VisualClock {
  simulationSeconds: number;
  world: MissionWorld;
  previous: MissionWorld;
  accumulator: number;
  lastFrame: number | null;
}
export function createClock(world: MissionWorld): VisualClock {
  return { world, previous: world, simulationSeconds: 0, accumulator: 0, lastFrame: null };
}
/** Reset interpolation and wall time at pause/visibility boundaries. */
export function resetClock(clock: VisualClock): VisualClock {
  return { ...clock, previous: clock.world, accumulator: 0, lastFrame: null };
}
export function advanceClock(clock: VisualClock, elapsedSeconds: number, paused = false): VisualClock {
  if (paused) return resetClock(clock);
  const delta = Number.isFinite(elapsedSeconds) ? Math.max(0, Math.min(MAX_FRAME_DELTA, elapsedSeconds)) : 0;
  let accumulator = clock.accumulator + delta;
  let world = clock.world;
  let previous = clock.previous;
  while (accumulator + 1e-10 >= STEP) {
    previous = world;
    world = stepWorld(world);
    accumulator = Math.max(0, accumulator - STEP);
  }
  // 1× accepted foreground time, independent of fleet or waypoint progress.
  // Pause/reset never discards this time, including its sub-step remainder.
  return { ...clock, world, previous, accumulator, simulationSeconds: clock.simulationSeconds + delta };
}
/** RAF timestamp adapter; a resumed frame establishes a new time origin. */
export function frameClock(clock: VisualClock, nowMs: number, paused = false): VisualClock {
  if (paused || !Number.isFinite(nowMs)) return resetClock(clock);
  if (clock.lastFrame === null) return { ...clock, lastFrame: nowMs };
  return { ...advanceClock(clock, (nowMs - clock.lastFrame) / 1000), lastFrame: nowMs };
}
