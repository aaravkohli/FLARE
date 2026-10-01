/** A path score and the decision's mission-level classification are separate evidence. */
export function telemetryThreatRows(
  routeLabel: string,
  routeScore: number | undefined,
  reportedThreatLevel: string | undefined,
): Array<[string, string]> {
  return [
    [`${routeLabel} threat score`, typeof routeScore === 'number' && Number.isFinite(routeScore) ? routeScore.toFixed(2) : 'Unavailable'],
    ['Reported mission threat', reportedThreatLevel?.trim() || 'Unavailable'],
  ];
}
