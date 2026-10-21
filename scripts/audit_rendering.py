"""Fresh-process, sustained renderer gates; run after other CPU/GPU audit jobs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from scripts.audit_support import protected_files


def main():
 root=Path(__file__).resolve().parents[1];output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True);before=protected_files(root);rows=[]
 for view in ('2d','3d','compare'):
  folder=output/view;folder.mkdir();env=dict(os.environ,FLARE_TEST_PRODUCTION='1',FLARE_MESH_PROFILE='1',FLARE_PROFILE_VIEW=view,FLARE_PROFILE_SECONDS='60',FLARE_ARTIFACT_DIR=str(folder))
  command=['npx','playwright','test','e2e/mesh-performance.spec.ts','--reporter=json','--trace','off'];start=time.monotonic()
  with (folder/'playwright.json').open('w') as out,(folder/'services.log').open('w') as err:result=subprocess.run(command,cwd=root/'frontend-react',env=env,stdout=out,stderr=err,timeout=360)
  row=dict(capability='100 UAV '+view,status='Passed' if result.returncode==0 else 'Failed',exit_code=result.returncode,duration_s=time.monotonic()-start,command=command,evidence=str(folder/'performance.json'));rows.append(row);(output/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
 assert protected_files(root)==before

if __name__=='__main__':main()
