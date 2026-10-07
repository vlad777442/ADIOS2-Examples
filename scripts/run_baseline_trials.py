#!/usr/bin/env python3
"""Controller for randomized repeated one/two-client independent-job baselines.
No faults, cache dropping, pool creation, deletion, or disk preparation.
Run on node0. Every job reads the full input using its client's separate RBD image.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import random
import re
import shlex
import subprocess
import time
import uuid

SSH=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10']

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trials',type=int,default=5,help='Trials per condition')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--mpi-procs',type=int,default=8)
    p.add_argument('--expected-steps',type=int,default=3600)
    p.add_argument('--input',default='/mnt/grayscott/gray-scott/gs-900g.bp')
    p.add_argument('--data-pool',default='grayscott-900g')
    p.add_argument('--repo',default='/users/vlad777/ADIOS2-Examples')
    p.add_argument('--output-dir',required=True,help='Local directory for schedule/logs and copied result files')
    p.add_argument('--start-lead',type=float,default=60,help='Seconds available for both clients to preflight before shared UTC start')
    a=p.parse_args()
    if min(a.trials,a.mpi_procs,a.expected_steps)<=0 or a.start_lead<10:p.error('Positive counts and start-lead >=10 required')
    root=Path(a.output_dir);root.mkdir(parents=True,exist_ok=False)
    session='baseline-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    order=['single','dual']*a.trials;random.Random(a.seed).shuffle(order)
    schedule={'session':session,'seed':a.seed,'order':order,'cache_policy':'natural; no cache manipulation',
              'condition':'independent full-input jobs; dual is not a distributed single job','trials':[]}
    jobs=[];trial=None
    try:
        for i,condition in enumerate(order):
            trial=f'{session}-{i:02d}-{condition}';barrier=time.time()+a.start_lead
            hosts=['node12'] if condition=='single' else ['node12','node13']
            jobs=[]
            for host in hosts:
                remote_results=f'{a.repo}/results/paper-baselines/{trial}'
                output=f'/mnt/grayscott/analysis/{trial}-{host}.bp'
                args=['sudo','-u','vlad777','python3',a.repo+'/scripts/run_recovery_experiment.py',
                      '--no-fault','--mpi-procs',str(a.mpi_procs),'--expected-steps',str(a.expected_steps),
                      '--input',a.input,'--output',output,'--output-dir',remote_results,
                      '--expected-data-pool',a.data_pool,'--trial-id',trial,'--start-at',str(barrier),'--collect-osd-perf']
                log=(root/(trial+'-'+host+'.log')).open('w')
                process=subprocess.Popen([*SSH,'root@'+host,shlex.join(args)],stdout=log,stderr=subprocess.STDOUT)
                jobs.append((host,process,log,remote_results))
            results=[]
            for host,process,log,remote_results in jobs:
                code=process.wait();log.close()
                # Copy only measurement artifacts from this unique trial directory.
                local=root/trial/host;local.mkdir(parents=True)
                subprocess.run(['scp','-r','root@'+host+':'+remote_results,str(local)],check=True)
                files=list(local.rglob('results.json'))
                if code or len(files)!=1:raise RuntimeError(f'{trial} {host}: invalid job exit={code}')
                result=json.loads(files[0].read_text())
                if result['trial_status']!='complete':raise RuntimeError(f'{trial} {host}: incomplete trial')
                results.append(result)
            starts=[datetime.fromisoformat(r['t_analysis_start']).timestamp() for r in results]
            ends=[datetime.fromisoformat(r['t_analysis_end']).timestamp() for r in results]
            span=max(ends)-min(starts)
            record={'trial_id':trial,'condition':condition,'start_skew_s':max(starts)-min(starts),
                    'aggregate_logical_mib_s':sum(r['logical_input_bytes'] for r in results)/1024**2/span,
                    'jobs':[{'host':r['hostname'],'duration_s':r['duration_analysis_s'],'logical_mib_s':r['logical_throughput_mib_s']} for r in results]}
            schedule['trials'].append(record)
            (root/'schedule.json').write_text(json.dumps(schedule,indent=2))
    except BaseException as e:
        schedule['error']=str(e);raise
    finally:
        # On interruption/failure, stop only runners with this exact trial ID.
        for host,process,log,remote_results in jobs:
            if process.poll() is None:
                code=("import glob,json,os,signal; "
                      f"files=glob.glob({remote_results!r}+'/recovery-experiment-*/results.json'); "
                      "records=[json.load(open(p)) for p in files]; "
                      f"trial={trial!r}; "
                      "pids=[r['runner_pid'] for r in records if r.get('args',{}).get('trial_id')==trial]; "
                      "[(os.kill(pid,signal.SIGTERM)) for pid in pids if os.path.exists('/proc/'+str(pid)+'/cmdline') and trial.encode() in open('/proc/'+str(pid)+'/cmdline','rb').read().split(bytes([0]))]")
                try:
                    subprocess.run([*SSH,'root@'+host,shlex.join(['python3','-c',code])],timeout=30,check=False)
                    process.wait(timeout=45)
                except (subprocess.TimeoutExpired,OSError):process.terminate()
            log.close()
        (root/'schedule.json').write_text(json.dumps(schedule,indent=2))
    print(root/'schedule.json')
if __name__=='__main__':main()
