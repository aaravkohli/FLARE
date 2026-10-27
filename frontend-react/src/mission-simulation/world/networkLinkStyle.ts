export type NetworkLinkLevel = 'background' | 'neighbor' | 'route' | 'candidate' | 'recovery';
export const NETWORK_LINK_WIDTHS = {background: 1.5, neighbor: 2.2, route: 3.2, candidate: 3.2,recovery:2.2} as const;
const healthyColors = {background: '#88b4c4', neighbor: '#9ad5e4', route: '#91e1c1', candidate: '#b5a1ea',recovery:'#9ad5e4'} as const;
export function networkLinkColor(status: string, level: NetworkLinkLevel, qualityColors=true) {
  if (status === 'blocked') return '#dc8e7e';
  if (qualityColors&&(status === 'jammed' || status === 'degraded')) return '#e4b77c';
  if (status === 'unavailable') return '#94a3ae';
  return healthyColors[level];
}
export type LinkEmphasis = 'subtle'|'standard'|'strong';
export const linkEmphasisScale = (value:LinkEmphasis='standard') => value==='strong'?1.65:value==='subtle'?.7:1;
export const backgroundLinkOpacity = (count: number) => count > 1000 ? .3 : count > 300 ? .48 : .7;
