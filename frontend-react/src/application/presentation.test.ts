import test from 'node:test';
import assert from 'node:assert/strict';
import { compare, destination, federationForSession, fleetMeasurement, matches, metric, signed, updateLabel } from './presentation.ts';

test('navigation accepts only application destinations', () => {
  assert.equal(destination('#mission'), 'mission');
  assert.equal(destination('#/security'), 'security');
  assert.equal(destination('#unknown'), 'overview');
});
test('missing values are not zero; measured zero and exact signed attribution survive formatting', () => {
  assert.equal(metric(null), '—'); assert.equal(metric(undefined), '—'); assert.equal(metric(NaN), '—');
  assert.equal(metric(0, 1, ' ms'), '0.0 ms');
  assert.equal(signed(-0.0000000134), '-1.34e-8'); assert.equal(signed(0.025), '+0.025');
});
test('natural ordering and null-last sorting work in both directions', () => {
  const values = [null, 3, NaN, 0, 1];
  assert.deepEqual(values.toSorted((a, b) => compare(a, b)).slice(0, 3), [0, 1, 3]);
  assert.deepEqual(values.toSorted((a, b) => compare(a, b, true)).slice(0, 3), [3, 1, 0]);
  assert.deepEqual(['drone_10', 'drone_2'].toSorted(compare), ['drone_2', 'drone_10']);
  assert.equal(matches(' SURVEY ', 'Survey UAV', 'drone_1'), true);
});
test('aggregation labels preserve no-update and pending independently of threat labels', () => {
  assert.equal(updateLabel({status:'NO_UPDATE',action:'PENDING'}), 'No update');
  assert.equal(updateLabel({status:'SUSPICIOUS',action:'ACCEPTED'}), 'Accepted');
  assert.equal(updateLabel({action:'DOWN_WEIGHTED'}), 'Down-weighted');
  assert.equal(updateLabel({action:'REJECTED'}), 'Rejected');
  assert.equal(updateLabel({action:'PENDING'}), 'Pending');
  assert.equal(updateLabel({}), 'Unavailable');
});
test('fleet metrics use confirmed installed family and current-session observations only', () => {
  const mission = {session_id:'current',flows:[{source:'drone_1',destination:'base',traffic_class:'telemetry',family:'mesh'}]} as any;
  const payload = {event:{mission_context:{session_id:'current'},telemetry:{source:'packet_simulation'},decision:{installed_path:'direct'}},decision:{path_name:'direct'},metrics:{paths:[{path_id:'mesh',pdr:0,latency:12},{path_id:'direct',pdr:1,latency:1}]}} as any;
  assert.deepEqual(fleetMeasurement('drone_1',mission,payload),{route:'mesh',pdr:0,latency:12,source:'packet_simulation'});
  assert.equal(fleetMeasurement('drone_1',{...mission,session_id:'new'},payload).pdr,null);
  assert.equal(fleetMeasurement('drone_2',mission,payload).route,null);
  assert.equal(fleetMeasurement('drone_1',null,payload).route,'direct');
  delete payload.event.decision; payload.decision.sdn_applied=false;
  assert.equal(fleetMeasurement('drone_1',null,payload).route,null);
});

test('federation evidence tolerates initial HTTP/snapshot order while rejecting prior-session measurements', () => {
  const current = {session_id:'mission-2', latest:{round:1, threat_f1:0}};
  assert.equal(federationForSession(current,'mission-2'),current);
  assert.equal(federationForSession(current,'mission-3'),null);
  const observed = {latest:{mission_session:'mission-2',threat_f1:0}};
  assert.equal(federationForSession(observed,'mission-2'),observed);
  assert.equal(federationForSession({latest:{threat_f1:0}},'mission-2'),null);
  assert.equal(federationForSession(null,'mission-2'),null);
});
