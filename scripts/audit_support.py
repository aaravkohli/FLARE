"""First-party audit discovery and disposable runtime copies; no live mutations."""
from __future__ import annotations
import ast
import csv
import hashlib
import json
from pathlib import Path
import shutil

OWNED = ('api','baselines','datasets','fleet','fl','orchestrator','provenance','rl','runtime','schemas','sdn','security','simulation','validation','scripts')

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def protected_files(root: Path) -> dict[str,str]:
    paths=list((root/'config').glob('*.yaml'))+list((root/'models').glob('*'))
    paths += [root/'simulation'/name for name in ('jam_state.json','insider_state.json','byzantine_state.json')]
    paths += list((root/'runtime').glob('*.json'))
    return {str(p.relative_to(root)):digest(p) for p in paths if p.is_file()}

def runtime_copy(root: Path, destination: Path) -> Path:
    """Copy mutable config/models and source, including uncommitted repairs.

    No symlinks/hardlinks to operator inputs: legacy writers resolve __file__.
    Frontend assets needed by Python terrain/provenance are copied separately.
    """
    destination.mkdir(parents=True,exist_ok=False)
    for directory in OWNED+('config','tests'):
        source=root/directory
        if source.exists():
            for path in source.rglob('*'):
                if path.is_file() and not {'__pycache__','.pytest_cache'}.intersection(path.parts) and path.suffix in ('.py','.yaml','.json','.csv','.cc','.sh','.ps1'):
                    target=destination/path.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for source in (root/'models').glob('*'):
        if source.is_file():
            target=destination/'models'/source.name;target.parent.mkdir(exist_ok=True);shutil.copy2(source,target)
    deployed=root/'models/deployments'
    if deployed.exists():shutil.copytree(deployed,destination/'models/deployments')
    for directory in ('results','logs','datasets/processed'):(destination/directory).mkdir(parents=True,exist_ok=True)
    for source in root.iterdir():
        if source.is_file() and source.suffix in ('.md','.yaml','.yml'):shutil.copy2(source,destination/source.name)
    for name in ('pytest.ini','requirements.txt'):
        if (root/name).exists():shutil.copy2(root/name,destination/name)
    for name in ('.github','docker','results'):
        if (root/name).exists():
            for source in (root/name).glob('**/*' if name!='results' else '*'):
                if source.is_file() and (name=='docker' or source.suffix in ('.json','.csv','.yaml','.yml','.sh','.md')):
                    target=destination/source.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    for source in (root/'frontend-react/src').rglob('*'):
        if source.is_file() and source.suffix in ('.ts','.tsx','.json'):
            target=destination/source.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    (destination/'audit-source.json').write_text(json.dumps({'root':str(root),'protected':protected_files(root)},indent=2))
    return destination

def discover(root: Path, output: Path):
    output.mkdir(parents=True,exist_ok=True)
    functions=[];routes=[];tests=[];calls=[];cli=[]
    class Visitor(ast.NodeVisitor):
        def __init__(self,path):self.path=path;self.stack=[]
        def visit_ClassDef(self,node):
            self.stack.append(node.name);self.generic_visit(node);self.stack.pop()
        def visit_FunctionDef(self,node):
            name='.'.join([*self.stack,node.name]); identity=f'{self.path}:{name}'
            entry={'id':identity,'file':self.path,'symbol':name,'line':node.lineno,'status':'Not Tested','evidence':[],
                   'assertions':sum(isinstance(n,ast.Assert) for n in ast.walk(node))}
            (tests if self.path.startswith('tests/') else functions).append(entry)
            for dec in node.decorator_list:
                if isinstance(dec,ast.Call):
                    method=dec.func.attr if isinstance(dec.func,ast.Attribute) else ''
                    if method in ('get','post','put','patch','delete','websocket'):
                        routes.append(dict(entry,method=method.upper(),path=dec.args[0].value if dec.args and isinstance(dec.args[0],ast.Constant) else '<dynamic>',surface='internal service' if self.path.startswith(('sdn/','simulation/')) else 'frontend/API'))
                    elif isinstance(dec.func,ast.Name) and dec.func.id=='route':
                        routes.append(dict(entry,method='RYU WSGI',path=ast.unparse(dec),surface='internal service'))
            self.stack.append(node.name);self.generic_visit(node);self.stack.pop()
        visit_AsyncFunctionDef=visit_FunctionDef
        def visit_Lambda(self,node):
            name='.'.join([*self.stack,f'lambda@{node.lineno}:{node.col_offset}'])
            entry={'id':f'{self.path}:{name}','file':self.path,'symbol':name,'line':node.lineno,'status':'Not Tested','evidence':[],'assertions':0}
            (tests if self.path.startswith('tests/') else functions).append(entry)
            self.generic_visit(node)
        def visit_Call(self,node):
            if isinstance(node.func,(ast.Name,ast.Attribute)):
                calls.append({'file':self.path,'line':node.lineno,'caller':'.'.join(self.stack),'target':ast.unparse(node.func)})
                if isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument':cli.append({'file':self.path,'line':node.lineno,'definition':ast.unparse(node)})
            self.generic_visit(node)
    for directory in OWNED+('tests',):
        for path in sorted((root/directory).rglob('*.py')):
            try:Visitor(str(path.relative_to(root))).visit(ast.parse(path.read_text()))
            except (SyntaxError,UnicodeError):pass
    for path in sorted((root/'frontend-react/scripts').glob('*.py')):
        Visitor(str(path.relative_to(root))).visit(ast.parse(path.read_text()))
    controls=[]
    import re
    for path in sorted((root/'frontend-react').rglob('*')):
        if {'node_modules','dist','test-results'}.intersection(path.parts) or path.suffix not in ('.tsx','.ts'):continue
        source=path.read_text()
        for match in re.finditer(r'<(button|input|select|textarea|summary|form)\b([^>]*)',source):
            controls.append({'id':f'{path.relative_to(root)}:{source[:match.start()].count(chr(10))+1}:{match.start()}', 'file':str(path.relative_to(root)),'tag':match[1],'definition':match[0][:800],'status':'Not Tested'})
    payload={'functions':functions,'routes':routes,'test_functions':tests,'calls':calls,'cli_arguments':cli,'source_controls':controls}
    for name,rows in payload.items():
        (output/f'{name}.json').write_text(json.dumps(rows,indent=2))
        if rows:
            with (output/f'{name}.csv').open('w') as handle:
                writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return {key:len(rows) for key,rows in payload.items()}
