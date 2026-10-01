import type { CommunicationRoute } from '../types.ts';

export type RouteAppearance = 'installed' | 'requested' | 'alternative' | 'inactive' | 'hidden';
export interface RouteVisualStatus {
  installed: boolean;
  candidate: boolean;
  hold: boolean;
}

/** Presentation only; the canonical installed/requested/hold decision is upstream. */
export function routeAppearance(status: RouteVisualStatus, matchingDrone: boolean, available: boolean, showAlternatives: boolean): RouteAppearance {
  if (!matchingDrone) return 'hidden';
  if (status.hold) return showAlternatives && available ? 'inactive' : 'hidden';
  if (status.installed) return 'installed';
  if (status.candidate) return 'requested';
  return showAlternatives && available ? 'alternative' : 'hidden';
}

export function routeLegRange(route: Extract<CommunicationRoute, 'direct' | 'satellite'>, leg: number): readonly [number, number] {
  if (route === 'satellite') return leg === 0 ? [0, .5] : [.5, 1];
  return [0, 1];
}

export function requestedAnnotation(failed: boolean): string {
  return failed ? 'REQUESTED · FAILED' : 'REQUESTED · NOT CONFIRMED';
}
