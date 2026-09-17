import { useEffect, useLayoutEffect, useRef } from 'react';
import type { RefObject } from 'react';
import { reconcileFleet, STEP } from './dynamics.ts';
import { frameClock, resetClock } from './clock.ts';
import type { VisualClock } from './clock.ts';
import { bodyTransform, interpolateDrone, pathGeometry, projectPosition } from './projection.ts';
import { MISSION_SATELLITE_ORBIT, satelliteOrbitAt } from './satelliteOrbit.ts';
import { satelliteRenderPosition } from './satelliteRender.ts';

/** Transient renderer bridge. React owns elements and network state; this hook
 * alone owns their frame-level geometry. No setState or network writes per frame.
 */
export function useSvgWorld(
  svg: RefObject<SVGSVGElement | null>, clock: RefObject<VisualClock>,
  ids: readonly string[], selectedId: string, paused: boolean,
) {
  const draw = useRef<() => void>(() => {});

  useLayoutEffect(() => {
    const world = reconcileFleet(clock.current.world, ids);
    if (world !== clock.current.world) clock.current = { ...clock.current, world, previous: world };
    const root = svg.current;
    if (!root) return;
    const markers = Array.from(root.querySelectorAll<SVGGElement>('[data-world-drone]')).map(node => ({
      id: node.dataset.worldDrone!, node, body: node.querySelector<SVGGElement>('.mission-drone__body'),
    }));
    const shadows = Array.from(root.querySelectorAll<SVGEllipseElement>('[data-world-shadow]'));
    const links = (['direct', 'satellite', 'mesh'] as const).map(route => ({
      route, paths: root.querySelectorAll<SVGPathElement>(`.mission-route--${route} path`),
    }));
    const note = root.querySelector<SVGGElement>('.mission-airspace-note');
    const anomaly = root.querySelector<SVGGElement>('.mission-anomaly');
    const patrol = root.querySelector<SVGPathElement>('.mission-patrol');
    const waypoint = root.querySelector<SVGCircleElement>('.mission-waypoint');
    const satellite = root.querySelector<SVGGElement>('.mission-satellite');
    draw.current = () => {
      const c = clock.current;
      const orbitalState = satelliteOrbitAt(MISSION_SATELLITE_ORBIT, MISSION_SATELLITE_ORBIT.config.epochSeconds + c.simulationSeconds);
      const satellitePoint = satelliteRenderPosition(orbitalState);
      // Evaluated even with an empty fleet. Render coordinates alone reach SVG.
      satellite?.setAttribute('transform', `translate(${satellitePoint.x} ${satellitePoint.y})`);
      const before = new Map(c.previous.drones.map(drone => [drone.id, drone]));
      const drones = new Map(c.world.drones.map(drone => [drone.id, interpolateDrone(before.get(drone.id) || drone, drone, c.accumulator / STEP)]));
      for (const marker of markers) {
        const drone = drones.get(marker.id);
        if (!drone) continue;
        const point = projectPosition(drone.position);
        marker.node.setAttribute('transform', `translate(${point.x} ${point.y})`);
        marker.body?.setAttribute('transform', bodyTransform(drone));
      }
      for (const shadow of shadows) {
        const drone = drones.get(shadow.dataset.worldShadow!);
        if (!drone) continue;
        const ground = projectPosition({ ...drone.position, y: 0 });
        shadow.setAttribute('cx', String(ground.x));
        shadow.setAttribute('cy', String(ground.y));
      }
      const selected = drones.get(selectedId);
      for (const link of links) for (const path of link.paths) path.style.display = selected ? '' : 'none';
      if (note) note.style.display = selected ? '' : 'none';
      if (!selected) {
        patrol?.setAttribute('d', '');
        if (waypoint) waypoint.style.display = 'none';
        return;
      }
      const point = projectPosition(selected.position);
      // No authoritative mesh hop identity: do not draw a fabricated forwarding chain.
      for (const link of links) {
        const geometry = pathGeometry(link.route, point, satellitePoint);
        for (const path of link.paths) { path.setAttribute('d', geometry); if (link.route === 'mesh') path.style.display = 'none'; }
      }
      anomaly?.setAttribute('transform', `translate(${point.x + 30} ${point.y - 44})`);
      patrol?.setAttribute('d', selected.waypoints.map((position, index) => {
        const p = projectPosition(position);
        return `${index ? 'L' : 'M'}${p.x} ${p.y}`;
      }).join(' ') + ' Z');
      const target = projectPosition(selected.target);
      if (waypoint) {
        waypoint.style.display = '';
        waypoint.setAttribute('cx', String(target.x));
        waypoint.setAttribute('cy', String(target.y));
      }
    };
    draw.current();
  });

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    const frozen = () => paused || reducedMotion.matches || document.hidden;
    const animate = (now: number) => {
      clock.current = frameClock(clock.current, now, frozen());
      draw.current();
      if (!frozen()) frame = requestAnimationFrame(animate);
    };
    const reset = () => {
      cancelAnimationFrame(frame);
      clock.current = resetClock(clock.current);
      svg.current?.setAttribute('data-world-frozen', String(frozen()));
      draw.current();
      if (!frozen()) frame = requestAnimationFrame(animate);
    };
    document.addEventListener('visibilitychange', reset);
    reducedMotion.addEventListener('change', reset);
    reset();
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', reset);
      reducedMotion.removeEventListener('change', reset);
      clock.current = resetClock(clock.current);
    };
  }, [clock, paused, svg]);
}
