import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { NETWORK_LINK_WIDTHS, backgroundLinkOpacity, linkEmphasisScale } from './networkLinkStyle.ts';
import type { LinkEmphasis, NetworkLinkLevel } from './networkLinkStyle.ts';

type Point = {x: number; y: number; z: number};
/** Four fixed buffers, independent of frame count and topology switching. */
export function createNetworkLinkLayer() {
  const root = new THREE.Group(), color = new THREE.Color();
  const palette = new Map(['#dc8e7e','#e4b77c','#94a3ae','#88b4c4','#9ad5e4','#91e1c1','#b5a1ea'].map(hex => [hex, new THREE.Color(hex)]));
  const levels: NetworkLinkLevel[] = ['background', 'neighbor', 'route', 'candidate'];
  const batches = levels.map((level, index) => {
    const capacity = level === 'background' ? 10000 : 256;
    const positions = new Float32Array(capacity * 6), colors = new Float32Array(capacity * 6);
    const distances = new Float32Array(capacity * 2);
    const geometry = new LineSegmentsGeometry().setPositions(positions).setColors(colors);
    const distanceBuffer = new THREE.InstancedInterleavedBuffer(distances, 2).setUsage(THREE.DynamicDrawUsage);
    geometry.setAttribute('instanceDistanceStart', new THREE.InterleavedBufferAttribute(distanceBuffer, 1, 0));
    geometry.setAttribute('instanceDistanceEnd', new THREE.InterleavedBufferAttribute(distanceBuffer, 1, 1));
    const positionBuffer = (geometry.getAttribute('instanceStart') as THREE.InterleavedBufferAttribute).data;
    const colorBuffer = (geometry.getAttribute('instanceColorStart') as THREE.InterleavedBufferAttribute).data;
    positionBuffer.setUsage(THREE.DynamicDrawUsage); colorBuffer.setUsage(THREE.DynamicDrawUsage);
    geometry.instanceCount = 0;
    const foreground = level === 'route' || level === 'candidate';
    const material = new LineMaterial({vertexColors: true, linewidth: NETWORK_LINK_WIDTHS[level], transparent: true,
      opacity: foreground ? .98 : .88, depthWrite: false, depthTest: !foreground, toneMapped: false,
      dashed: level === 'candidate', dashSize: 9, gapSize: 6});
    const line = new LineSegments2(geometry, material);
    line.frustumCulled = false; line.renderOrder = foreground ? 5 + index : 1 + index; root.add(line);
    let outline: LineSegments2 | undefined;
    if (foreground) {
      const outlineMaterial = new LineMaterial({color: '#101d25', linewidth: 6.2, transparent: true, opacity: .68,
        depthTest: false, depthWrite: false, toneMapped: false, dashed: level === 'candidate', dashSize: 9, gapSize: 6});
      outline = new LineSegments2(geometry, outlineMaterial); outline.frustumCulled = false; outline.renderOrder = line.renderOrder - .5; root.add(outline);
    }
    return {level, capacity, positions, colors, distances, geometry, material, outline, positionBuffer, colorBuffer, distanceBuffer, count: 0};
  });
  const byLevel = Object.fromEntries(batches.map(batch => [batch.level, batch])) as Record<NetworkLinkLevel, typeof batches[number]>;
  let disposed = false;
  return {
    root,
    begin(linkCount: number, emphasis:LinkEmphasis='standard') {
      const scale=linkEmphasisScale(emphasis);
      for (const batch of batches) {
        batch.count=0;
        batch.material.linewidth=NETWORK_LINK_WIDTHS[batch.level]*scale;
        batch.material.opacity=batch.level==='background'?Math.min(.94,backgroundLinkOpacity(linkCount)*scale):batch.level==='neighbor'?Math.min(.98,.88*scale):.98;
        if(batch.outline)batch.outline.material.linewidth=6.2*scale;
      }
    },
    add(level: NetworkLinkLevel, a: Point, b: Point, hex: string, distance = 0) {
      const batch = byLevel[level]; if (batch.count >= batch.capacity) return;
      const offset = batch.count * 6, tint = palette.get(hex) ?? color.set(hex);
      const p = batch.positions, c = batch.colors;
      p[offset]=a.x;p[offset+1]=a.y;p[offset+2]=a.z;p[offset+3]=b.x;p[offset+4]=b.y;p[offset+5]=b.z;
      c[offset]=c[offset+3]=tint.r;c[offset+1]=c[offset+4]=tint.g;c[offset+2]=c[offset+5]=tint.b;
      batch.distances[batch.count * 2] = distance;
      batch.distances[batch.count * 2 + 1] = distance + Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
      batch.count++;
    },
    commit(width: number, height: number, visible: boolean) {
      root.visible = visible;
      for (const batch of batches) {
        batch.geometry.instanceCount = batch.count;
        batch.material.resolution.set(width, height); batch.outline?.material.resolution.set(width, height);
        for (const [buffer, length] of [[batch.positionBuffer, batch.count * 6], [batch.colorBuffer, batch.count * 6], [batch.distanceBuffer, batch.count * 2]] as const) {
          buffer.clearUpdateRanges(); if (length) {buffer.addUpdateRange(0, length); buffer.needsUpdate = true;}
        }
      }
    },
    counts() { return Object.fromEntries(batches.map(batch => [batch.level, batch.count])); },
    dispose() {
      if (disposed) return; disposed = true;
      for (const batch of batches) {batch.geometry.dispose(); batch.material.dispose(); batch.outline?.material.dispose();}
      root.clear(); root.removeFromParent();
    },
  };
}
