export interface LabelBox { x:number; y:number; width:number; height:number }
export const CRAFT_MARKER_SIZE_PX = Object.freeze({ normal: 10, hovered: 12, selected: 14 });
/** Identification glyphs use screen pixels; their size is never a world measurement. */
export function craftMarkerBox(x: number, y: number, selected: boolean, hovered: boolean): LabelBox {
  const size = selected ? CRAFT_MARKER_SIZE_PX.selected : hovered ? CRAFT_MARKER_SIZE_PX.hovered : CRAFT_MARKER_SIZE_PX.normal;
  return { x: x - size / 2, y: y - size / 2, width: size, height: size };
}
/** Stable candidate ordering; priority labels can search adjacent diagonals when a compact fleet blocks every cardinal position. */
export function placeLabel(x:number,y:number,width:number,height:number,viewportWidth:number,viewportHeight:number,occupied:readonly LabelBox[],priority=false):LabelBox|null {
  const candidates = [[0,18],[0,-height-20],[width/2+20,-height/2],[-width/2-20,-height/2]];
  if(priority) candidates.push(
    [-width/2-20,18],[width/2+20,18],
    [-width/2-20,-height-20],[width/2+20,-height-20],
    [0,height+28],[0,-2*height-28],
  );
  for(const [dx,dy] of candidates) {
    const box={x:x+dx-width/2,y:y+dy,width,height};
    if(box.x<8||box.y<8||box.x+width>viewportWidth-8||box.y+height>viewportHeight-8)continue;
    if(occupied.some(b=>box.x<b.x+b.width+8&&box.x+width+8>b.x&&box.y<b.y+b.height+5&&box.y+height+5>b.y))continue;
    return box;
  }
  return null;
}
export function compactDroneId(id:string,index:number) { return /^drone_\d+$/.test(id)?'D'+id.slice(6):'D'+(index+1); }

/** Y is the local simulation altitude, not height above terrain or a GPS value. */
export function worldDroneLabel(identity: string, altitudeM: number, selected: boolean, hovered: boolean, installedRoute?: string | null): string {
  if (!selected && !hovered) return identity;
  const installed = selected && installedRoute
    ? installedRoute === 'hold' ? ' · HOLD' : ` · INST ${installedRoute.toUpperCase()}`
    : '';
  return `${identity}${installed}\nSIM ALT ${Math.round(altitudeM)} m`;
}
