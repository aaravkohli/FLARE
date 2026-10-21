"""Record first-party calls per pytest case, separately from assertions/results."""
import json
import os
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
current=None
calls={}
results={}
_identities={}
_prefix=str(root)+'/'
def profile(frame,event,arg):
    if event!='call' or current is None:return
    code=frame.f_code
    identity=_identities.get(code)
    if identity is None:
        filename=code.co_filename
        if not filename.startswith(_prefix) or code.co_name.startswith('<'):identity=False
        else:
            path=filename[len(_prefix):]
            identity=False if path.startswith(('tests/','venv/')) else f"{path}:{code.co_qualname.replace('.<locals>.','.') }"
        _identities[code]=identity
    if identity:calls.setdefault(identity,set()).add(current)

def pytest_sessionstart(session):sys.setprofile(profile)
def pytest_runtest_setup(item):
    global current
    current=item.nodeid

def pytest_runtest_logreport(report):
    if report.when=='call' or report.failed or report.skipped:
        results[report.nodeid]={'outcome':report.outcome,'phase':report.when,'duration_s':report.duration}

def pytest_sessionfinish(session,exitstatus):
    sys.setprofile(None)
    target=Path(os.environ['FLARE_AUDIT_TRACE']);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'exit_code':int(exitstatus),'tests':results,'function_calls':{key:sorted(value) for key,value in calls.items()}},indent=2))
