"""Verify review artifacts and quoted numbers against captured source evidence.

This checks artifact integrity and specific numerical claims. It is not an
application audit, scientific replication, or automated proof of prose quality.
"""
from pathlib import Path
import hashlib
import json
import re
import sys
import zipfile
from urllib.parse import unquote

pack = Path(__file__).resolve().parents[1]
root = pack.parent
checks = []
def check(name, test):
    if not test:
        raise AssertionError(name)
    checks.append({'name':name,'status':'Passed'})
def read(name):
    return json.loads((pack / name).read_text())

required = ['README.md','DEMO.md','RESULTS.md','QUESTIONS.md','REVIEW_DAY.md','CONTRIBUTION.md',
            'presentation/output/FLARE-panel-review.pptx',
            'presentation/output/FLARE-speaker-script.md']
check('All requested review documents and deck exist', all((pack/n).is_file() and (pack/n).stat().st_size for n in required))
questions = (pack/'QUESTIONS.md').read_text()
check('45 sequential questions', list(map(int,re.findall(r'^### Q(\d+)\.', questions, re.M))) == list(range(1,46)))
check('Four question categories', all(f'## {name} questions' in questions for name in ['Basic','Intermediate','Technical','Challenging']))
check('Every question has a short and deeper answer',questions.count('**Say')==45 and questions.count('**If asked for depth:**')==45)
missing=[]
for file in pack.rglob('*.md'):
    if any(p.startswith('.') for p in file.relative_to(pack).parts): continue
    for target in re.findall(r'\]\(([^)]+)\)',file.read_text()):
        if target.startswith(('https:','http:','mailto:','#')):continue
        target=unquote(target.split('#')[0]).strip('<>')
        if not (file.parent/target).exists():missing.append((str(file.relative_to(pack)),target))
check('All local Markdown links resolve',not missing)

with zipfile.ZipFile(pack/'presentation/output/FLARE-panel-review.pptx') as z:
    names=z.namelist()
    check('Ten editable slides and ten notes parts',sum(bool(re.fullmatch(r'ppt/slides/slide\d+\.xml',n)) for n in names)==10 and sum(bool(re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml',n)) for n in names)==10)
    check('Two native chart workbooks',sum(n.endswith('.xlsx') for n in names)==2)
    check('Actual screenshots embedded',sum(n.startswith('ppt/media/') for n in names)>=4)

results=(pack/'RESULTS.md').read_text()
mesh=read('evidence/mesh-results.json')
check('Thirty complete benchmark observations',len(mesh['results'])==30 and {r['seed'] for r in mesh['results']}=={7,42,99} and len({r['topology'] for r in mesh['results']})==10)
check('Benchmark packet accounting',all(r['conservation'] and r['generated']==r['delivered']+r['dropped'] for r in mesh['results']))
for row in [r for r in mesh['results'] if r['seed']==42]:
    check('Seed-42 table values: '+row['topology'],f"{row['delivered']} / {row['generated']}" in results and f"{row['pdr']*100:.2f}%" in results and f"{row['average_latency_ms']:.2f} ms" in results)
routing=read('evidence/routing-evaluation.json')
check('All quoted v2 baseline means match evidence',all(f"{row['mean_episode_reward']:.2f}" in results for row in routing['v2']['policies'].values() if row is not routing['v2']['policies']['dqn_raw']))
byz=read('evidence/byzantine_aggregation.json')
check('Poisoning means match three-seed summary',all(f"{byz['summary'][k]['mean']*100:.2f}%" in results for k in ['clean_fedavg_accuracy','attacked_fedavg_accuracy','secure_accuracy']))
check('False flags distinguished from false rejections',byz['pooled_detection']['benign_updates_false_positive']==4 and byz['pooled_detection']['benign_updates_false_rejected']==0 and '4 / 36' in results and '0 / 36' in results)
for name in ['evidence/demo-probe-result.json','evidence/final-rehearsal/probe-result.json']:
    p=read(name)['data']
    check('Five-hop probe: '+name,p['status']=='delivered' and len(p['history'])==5 and p['retries']==0 and p['size_bytes']==512 and f"{(p['completed_at']-p['created_at'])*1000:.2f} ms" in results)
core=read('evidence/final-rehearsal/result.json')
extended=read('evidence/final-rehearsal/extended-result.json')
check('Core actual-backend execution passed',core['status']=='Passed' and core['actual_backend'] and not core['errors'])
check('Extended actual-backend assertions passed',len(extended['checks'])==6 and all(c['status']=='Passed' for c in extended['checks']) and not extended['errors'])
check('Methodological boundaries remain explicit',all(s in results.replace('**','') for s in ['55-second','dimensionless','not a field safety guarantee','4 / 36','34 passed, 2 failed, 1 skipped']))
check('Personal contribution is not invented','cannot infer your contribution' in questions)
contribution=(pack/'CONTRIBUTION.md').read_text()
check('Six complete conditional contribution scripts',contribution.count('## If you owned')==6 and '[the module you owned]' not in questions and 'Choose a statement below only if' in contribution)

manifest_file=pack/'evidence/manifest.json'
if manifest_file.exists():
    manifest=read('evidence/manifest.json')
    check('Manifest artifact hashes match',all((pack/item['path']).exists() and hashlib.sha256((pack/item['path']).read_bytes()).hexdigest()==item['sha256'] for item in manifest['artifacts']))
report={'scope':'Review artifact and quoted-value verification, not whole-application validation', 'status':'Passed','checks':checks}
print(json.dumps(report,indent=2))
