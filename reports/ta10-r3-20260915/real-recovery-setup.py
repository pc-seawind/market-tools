import os,shutil,json
from pathlib import Path
from mt1 import ta_pipeline as p
from mt1.ta_research import read,digest,save
report=Path('/home/emox/work/investment/reference/daily-reports/20260915T183106-evening');root=Path('reports/ta10-r3-20260915/REAL-recovery').resolve()
key=digest(p.identity(report))[:24]
source=Path('.cron_state/ta10-production/revisions/ea91ab87b32511cff2a0b06a/runs/ta10-908361f5eda4227ea1687749').resolve();dest=root/'revisions'/key
(dest/'runs').mkdir(parents=True,exist_ok=True);shutil.copytree(source,dest/'runs'/source.name,dirs_exist_ok=True);save(dest/'input.json',read(source/'input.json'))
# This is transport/recovery verification using actual archived model outputs,
# not a new research cohort. A deliberate local bus error rejects first launch.
os.environ['DBUS_SESSION_BUS_ADDRESS']='unix:path=/tmp/ta10-r3-deliberately-absent-bus'
a=p.request_refresh(report,'evening',root)
assert a['status']=='launch_failed',a
save(root/'first-failure-receipt.json',a);save(root/'context.json',{'key':key,'root':str(root),'report':str(report),'checkpoint_source':str(source),'mode':'REAL_systemd_recovery_archived_checkpoint_no_new_inference'})
print(json.dumps(a,ensure_ascii=False))
