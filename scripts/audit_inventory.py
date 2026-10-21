"""Reconcile static symbols, runtime registration, and assertion-backed evidence.

Executed function coverage is deliberately separate from verification outcomes.
"""
import ast
import csv
import json
import re
from pathlib import Path
import sys
import yaml
from scripts.audit_support import discover,OWNED,digest


def write(folder,name,rows):
 (folder/(name+'.json')).write_text(json.dumps(rows,indent=2))
 if rows:
  keys=list(dict.fromkeys(k for row in rows for k in row))
  with (folder/(name+'.csv')).open('w') as stream:
   w=csv.DictWriter(stream,fieldnames=keys);w.writeheader();w.writerows({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in r.items()} for r in rows)


def main():
 root=Path(__file__).resolve().parents[1];folder=Path(sys.argv[1]);evidence=Path(sys.argv[2]);discover(root,folder)
 trace=json.loads((evidence/'python-trace-baseline.json').read_text())
 if (evidence/'python-trace-repairs.json').exists():
  extra=json.loads((evidence/'python-trace-repairs.json').read_text());trace['tests'].update(extra['tests'])
  for key,value in extra['function_calls'].items():trace['function_calls'][key]=sorted(set(trace['function_calls'].get(key,[])+value))
 calls=trace['function_calls'];test_source={};static_calls=json.loads((folder/'calls.json').read_text())
 for path in (root/'tests').glob('*.py'):
  try:tree=ast.parse(path.read_text())
  except SyntaxError:continue
  for node in ast.walk(tree):
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_'):
    test_source[f'tests/{path.name}::{node.name}']=dict(assertions=sum(isinstance(x,ast.Assert) for x in ast.walk(node)),doc=ast.get_docstring(node),source=ast.get_source_segment(path.read_text(),node) or '')
 # Direct calls under an assertion are a narrow, reviewable assertion link.
 functions=json.loads((folder/'functions.json').read_text())
 for row in functions:
  executed=calls.get(row['id'],[]);row['executed_by']=executed;row['assertion_evidence']=[]
  row['surface']='internal service' if row['file'].startswith(('sdn/','api/','orchestrator/')) else 'CLI/research' if row['file'].startswith(('scripts/','baselines/','datasets/')) else 'internal library'
  leaf=row['symbol'].rsplit('.',1)[-1]
  for nodeid in executed:
   test=test_source.get(nodeid.split('[')[0]);
   if trace['tests'].get(nodeid,{}).get('outcome')=='passed' and test and test['assertions'] and re.search(r'assert[^\n]*\b'+re.escape(leaf)+r'\s*\(',test['source']):row['assertion_evidence'].append(nodeid)
  if row['assertion_evidence']:row.update(status='Passed',evidence=['python-baseline.xml','python-trace-baseline.json'],verification_scope='Direct asserted calls only; unexercised branches are not implied')
  else:row['verification_scope']='Execution-only evidence available' if executed else 'No fresh function-level execution evidence mapped'
  row['callers']=[dict(file=c['file'],line=c['line'],caller=c['caller']) for c in static_calls if c['target'].split('.')[-1]==leaf][:80]
 write(folder,'functions',functions)
 configs=[]
 def flatten(value,prefix,path):
  if isinstance(value,dict):
   for key,item in value.items():flatten(item,prefix+'.'+str(key) if prefix else str(key),path)
  else:configs.append(dict(id=path+':'+prefix,file=path,key=prefix,value_type=type(value).__name__,surface='configuration',status='Not Tested',evidence=[],note='Each leaf is accounted for; no Cartesian sweep of continuous values is claimed. Secret values are excluded.'))
 for path in (root/'config').glob('*.yaml'):flatten(yaml.safe_load(path.read_text()),'',str(path.relative_to(root)))
 write(folder,'configuration',configs)
 flags=[];messages=[]
 for directory in OWNED:
  for path in (root/directory).rglob('*.py'):
   source=path.read_text();tree=ast.parse(source)
   for node in ast.walk(tree):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='getenv' and node.args and isinstance(node.args[0],ast.Constant):flags.append(dict(id=str(path.relative_to(root))+':'+str(node.lineno),file=str(path.relative_to(root)),line=node.lineno,variable=node.args[0].value,status='Not Tested'))
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='event' and len(node.args)>1 and isinstance(node.args[1],ast.Constant):messages.append(dict(id=str(path.relative_to(root))+':'+str(node.lineno),file=str(path.relative_to(root)),line=node.lineno,protocol='mission event',message=node.args[1].value,status='Not Tested'))
   for match in re.finditer(r"['\"]type['\"]\s*:\s*['\"]([^'\"]+)['\"]",source):
    if path.parts[-2]=='api':messages.append(dict(id=str(path.relative_to(root))+':'+str(match.start()),file=str(path.relative_to(root)),line=source[:match.start()].count('\n')+1,protocol='WebSocket/SSE payload',message=match[1],status='Not Tested'))
 write(folder,'environment_switches',flags);write(folder,'stream_messages',messages)
 # Runtime endpoint names reconcile nested routers and aliases not discoverable
 # from decorator text alone. Imported from the disposable runtime by a caller.
 runtime=[]
 for name in ['api-runtime-routes.json','sdn-runtime-routes.json']:
  if (evidence/name).exists():runtime+=json.loads((evidence/name).read_text())
 for row in runtime:row.update(status='Not Tested',authorization='Not Tested',interface_applicability='internal' if row['service']=='sdn' else 'API',evidence=[])
 write(folder,'runtime_routes',runtime)
 # Include shell entry points and the asset-generation Python outside packages.
 tools=[]
 for path in [*(root/'scripts').glob('*.sh'),*(root/'scripts').glob('*.ps1'),*(root/'frontend-react').rglob('*.py')]:
  if 'node_modules' in path.parts:continue
  tools.append(dict(id=str(path.relative_to(root)),surface='CLI/research',status='Not Tested',sha256=digest(path)))
 write(folder,'additional_entrypoints',tools)
 cpp=[]
 for path in (root/'simulation/ns3').glob('*.cc'):
  source=path.read_text()
  for match in re.finditer(r'^([A-Za-z_]\w*)\([^;{}]*\)\s*\{',source,re.M):
   cpp.append(dict(id=str(path.relative_to(root))+':'+match[1],file=str(path.relative_to(root)),symbol=match[1],line=source[:match.start()].count('\n')+1,surface='CLI/research',status='Blocked',reason='ns-3 headers, libraries and runtime absent',evidence=['ns3-packet-traces-actual.log','wireless-mobility-actual.log']))
 write(folder,'cpp_functions',cpp)
 print(json.dumps(dict(functions=len(functions),executed=sum(bool(r['executed_by']) for r in functions),direct_asserted=sum(r['status']=='Passed' for r in functions),configuration=len(configs),switches=len(flags),messages=len(messages),runtime_routes=len(runtime))))

if __name__=='__main__':main()
