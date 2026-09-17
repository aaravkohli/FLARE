/** Route scene picks without allowing asset identifiers into fleet selection. */
export function selectWorldObject(
  id: string | undefined,
  fleetIds: readonly string[],
  selectDrone: (id: string) => void,
  selectAsset: (id: 'base' | 'satellite' | 'jammer') => void,
) {
  if (id === 'base' || id === 'satellite' || id === 'jammer') selectAsset(id);
  else if (id && fleetIds.includes(id)) selectDrone(id);
}
