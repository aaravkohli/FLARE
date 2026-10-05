export const COMMANDS=['hold','resume','return','altitude','move','waypoints','formation','route','reroute','role','disconnect','reconnect','isolate','restore','emergency'];
export const ROLES=['standard','scout','relay','leader','sensor','gateway','surveillance','communication_relay','edge_computing','cluster_head'];
export function commandParameters(action:string,fields:Record<string,string|boolean>):Record<string,unknown>{
 const finite=(key:string)=>{const raw=fields[key];if(typeof raw!=='string'||!raw.trim()||!Number.isFinite(Number(raw)))throw new Error(`${key}: enter a finite number`);return Number(raw);};
 const altitude=(n:number)=>{if(n<35||n>300)throw new Error('Altitude must be 35–300 local metres');return n;};
 const horizontal=(n:number)=>{if(Math.abs(n)>10000)throw new Error('Horizontal coordinates must be within ±10000 metres');return n;};
 if(action==='altitude')return {altitude:altitude(finite('altitude'))};
 if(action==='move')return {x:horizontal(finite('x')),y:altitude(finite('y')),z:horizontal(finite('z'))};
 if(action==='waypoints'){
  let points:unknown;try{points=JSON.parse(String(fields.points));}catch{throw new Error('Waypoints must be JSON: [[x,y,z], [x,y,z]]');}
  if(!Array.isArray(points)||points.length<1||points.length>64||points.some(p=>!Array.isArray(p)||p.length!==3||p.some(v=>typeof v!=='number'||!Number.isFinite(v))))throw new Error('Enter 1–64 finite [x,y,z] waypoints');
  for(const p of points){horizontal(p[0]);altitude(p[1]);horizontal(p[2]);}
  return {points,loop:fields.loop===true};
 }
 if(action==='route')return {family:fields.family};
 if(action==='role')return {role:fields.role};
 if(action==='formation')return {formation:fields.formation};
 return {};
}
