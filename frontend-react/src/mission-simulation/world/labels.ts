export interface LabelBox { x:number; y:number; width:number; height:number }
/** Stable candidate ordering; selected/hovered labels are placed first by the caller. */
export function placeLabel(x:number,y:number,width:number,height:number,viewportWidth:number,viewportHeight:number,occupied:readonly LabelBox[]):LabelBox|null {
  for(const [dx,dy] of [[0,18],[0,-height-20],[width/2+20,-height/2],[-width/2-20,-height/2]]) {
    const box={x:x+dx-width/2,y:y+dy,width,height};
    if(box.x<8||box.y<8||box.x+width>viewportWidth-8||box.y+height>viewportHeight-8)continue;
    if(occupied.some(b=>box.x<b.x+b.width+8&&box.x+width+8>b.x&&box.y<b.y+b.height+5&&box.y+height+5>b.y))continue;
    return box;
  }
  return null;
}
export function compactDroneId(id:string,index:number) { return /^drone_\d+$/.test(id)?'D'+id.slice(6):'D'+(index+1); }
