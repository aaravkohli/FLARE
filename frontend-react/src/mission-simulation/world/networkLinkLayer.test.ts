import test from 'node:test';
import assert from 'node:assert/strict';
import { createNetworkLinkLayer } from './networkLinkLayer.ts';
import { networkLinkColor, NETWORK_LINK_WIDTHS, backgroundLinkOpacity } from './networkLinkStyle.ts';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';

test('route prominence does not override blocked or degraded state, and dense background remains visible', () => {
  for (const level of ['background','neighbor','route','candidate'] as const) {
    assert.equal(networkLinkColor('blocked',level),'#dc8e7e');
    assert.equal(networkLinkColor('jammed',level),'#e4b77c');
  }
  assert.notEqual(networkLinkColor('healthy','route'),networkLinkColor('healthy','candidate'));
  assert.ok(NETWORK_LINK_WIDTHS.route>NETWORK_LINK_WIDTHS.neighbor&&NETWORK_LINK_WIDTHS.neighbor>NETWORK_LINK_WIDTHS.background);
  assert.ok(backgroundLinkOpacity(5050)>0&&backgroundLinkOpacity(5050)<backgroundLinkOpacity(30));
});
test('topology mode removes RF warning colors without disguising forwarding blocks or candidate routes',()=>{
 for(const level of ['background','neighbor','route','candidate'] as const){
  assert.equal(networkLinkColor('degraded',level,false),networkLinkColor('healthy',level,false));
  assert.equal(networkLinkColor('jammed',level,false),networkLinkColor('healthy',level,false));
  assert.equal(networkLinkColor('degraded',level,true),'#e4b77c');
  assert.equal(networkLinkColor('blocked',level,false),'#dc8e7e');
 }
 assert.notEqual(networkLinkColor('healthy','candidate',false),networkLinkColor('healthy','route',false));
});
test('wide links reuse bounded buffers, preserve endpoints, distinguish candidate dashes and clean up once', () => {
  const layer = createNetworkLinkLayer();
  const lines = layer.root.children as LineSegments2[];
  const geometries = [...new Set(lines.map(line=>line.geometry))], materials = lines.map(line=>line.material);
  const buffers = geometries.map(g=>g.getAttribute('instanceStart'));
  const a={x:1,y:80,z:3},b={x:50,y:100,z:-40};
  layer.begin(5050);
  for(let i=0;i<11000;i++)layer.add('background',a,b,'#88b4c4');
  layer.add('neighbor',a,b,'#9ad5e4');layer.add('route',a,b,'#91e1c1');layer.add('candidate',b,a,'#b5a1ea');
  layer.commit(915,500,true);
  assert.deepEqual(layer.counts(),{background:10000,neighbor:1,route:1,candidate:1,recovery:0});
  const route=lines.find(line=>line.material.vertexColors&&line.material.linewidth===3.2&&!line.material.dashed)!;
  const candidate=lines.find(line=>line.material.vertexColors&&line.material.dashed)!;
  assert.ok(route&&candidate);assert.equal(candidate.geometry.instanceCount,1);
  assert.deepEqual([route.geometry.getAttribute('instanceStart').getX(0),route.geometry.getAttribute('instanceEnd').getZ(0)],[1,-40]);
  assert.equal(route.material.resolution.x,915);
  layer.begin(10);layer.add('route',b,a,'#91e1c1');layer.commit(390,400,true);
  assert.deepEqual(geometries.map(g=>g.getAttribute('instanceStart')),buffers);
  assert.deepEqual(layer.counts(),{background:0,neighbor:0,route:1,candidate:0,recovery:0});
  assert.equal(route.material.linewidth,3.2);assert.equal(route.material.resolution.x,390);
  let geometryDisposals=0,materialDisposals=0;
  geometries.forEach(g=>g.addEventListener('dispose',()=>geometryDisposals++));materials.forEach(m=>m.addEventListener('dispose',()=>materialDisposals++));
  layer.dispose();layer.dispose();assert.equal(geometryDisposals,5);assert.equal(materialDisposals,7);assert.equal(layer.root.children.length,0);
});
test('emphasis changes width and opacity without replacing buffers or health colors',()=>{
 const layer=createNetworkLinkLayer(),lines=layer.root.children as LineSegments2[];
 const geometries=lines.map(line=>line.geometry);
 const background=lines.find(line=>line.material.vertexColors&&line.material.linewidth===1.5)!;
 layer.begin(30,'subtle');const subtleWidth=background.material.linewidth,subtleOpacity=background.material.opacity;
 layer.begin(30,'strong');assert.ok(background.material.linewidth>subtleWidth);assert.ok(background.material.opacity>subtleOpacity);
 assert.deepEqual(lines.map(line=>line.geometry),geometries);
 layer.begin(30,'standard');assert.equal(background.material.linewidth,1.5);layer.dispose();
});
