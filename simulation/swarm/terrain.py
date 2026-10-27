"""Decode the same offline terrain used by Three.js; no geographic RF claims."""
from __future__ import annotations
import base64
import json
import math
from pathlib import Path
import struct

def representative(x: float, z: float) -> float:
    return 13 * math.exp(-((x+220)**2/65000 + (z+140)**2/19000)) + 8 * math.exp(-((x-260)**2/24000 + (z-80)**2/43000)) - 3

class Terrain:
    def __init__(self, path: Path | None = None):
        path = path or Path(__file__).resolve().parents[2] / 'frontend-react/src/mission-simulation/assets/terrain/heightmap.ts'
        self.provenance = {'source':'representative','fallback':True,'extension':'representative_outside_patch'}
        try:
            text = path.read_text()
            self.grid = json.loads(text.split('export const TERRAIN_GRID = ')[1].split(' as const;')[0])
            encoded = text.split("export const HEIGHTMAP_BASE64 = '")[1].split("';")[0]
            data = base64.b64decode(encoded, validate=True)
            checksum = 2166136261
            for byte in data: checksum = ((checksum ^ byte) * 16777619) & 0xffffffff
            if f'{checksum:08x}' != self.grid['encodedFnv1a32']: raise ValueError('checksum mismatch')
            self.samples = struct.unpack('<' + 'H' * (len(data)//2), data)
            if len(self.samples) != self.grid['columns'] * self.grid['rows']: raise ValueError('sample count')
            self.provenance = {'source':'usgs-3dep-local-simulation','fallback':False,'extension':'representative_outside_patch','checksum':self.grid['encodedFnv1a32']}
        except (OSError, ValueError, IndexError, KeyError, struct.error):
            self.grid = {}; self.samples = ()
        # Decode immutable elevation values once. Radio checks sample the same
        # heightfield repeatedly; positions change, the committed samples do not.
        self._elevations = tuple(self.grid['elevationBaseM'] + sample*self.grid['elevationStepM'] for sample in self.samples)
        # Bilinear interpolation and the edge blend are convex combinations.
        # The analytic extension is at most 13 + 8 - 3 metres. Include a
        # conservative floating-point margin before certifying radio clearance.
        if self.samples:
            low, high = min(self._elevations), max(self._elevations)
            core_max = high - self.grid['maximumElevationM'] + self.grid['maxWorldHeightM']
            scale = max(18., abs(low), abs(high), abs(self.grid['maximumElevationM']), abs(self.grid['maxWorldHeightM']))
        else:
            core_max = scale = 18.
        self._height_ceiling = max(18., core_max) + 64*math.ulp(scale) if math.isfinite(core_max) and math.isfinite(scale) else math.inf

    def clears_heightfield(self, minimum_height: float, clearance: float) -> bool:
        # A custom sampler must not inherit a certificate for different terrain.
        return (type(self) is Terrain and getattr(self.height, '__func__', None) is _HEIGHT_IMPLEMENTATION
                and minimum_height > self._height_ceiling + clearance)

    def height(self, x: float, z: float) -> float:
        g = self.grid
        if not self.samples or abs(x) >= g['worldWidthM']/2 or abs(z) >= g['worldDepthM']/2:
            return representative(x, z)
        u = (x/g['worldWidthM']+.5)*(g['columns']-1)
        v = (z/g['worldDepthM']+.5)*(g['rows']-1)
        ix, iz = int(u), int(v); fx, fz = u-ix, v-iz
        lower = iz*g['columns']+ix; upper = lower+g['columns']; samples = self._elevations
        elevation = (samples[lower]*(1-fx)+samples[lower+1]*fx)*(1-fz)+(samples[upper]*(1-fx)+samples[upper+1]*fx)*fz
        # Match the renderer's zero reference and edge blend.
        core = elevation - g['maximumElevationM'] + g['maxWorldHeightM']
        edge = min(g['worldWidthM']/2-abs(x), g['worldDepthM']/2-abs(z))
        blend = min(1, max(0, edge/g['edgeBlendM'])); blend = blend*blend*(3-2*blend)
        if blend == 1: return core
        return core*blend + representative(x,z)*(1-blend)

_HEIGHT_IMPLEMENTATION = Terrain.height
