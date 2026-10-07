#!/usr/bin/env python3
"""Ceph recovery controls and structured polling for package/cephadm deployments.
Mutations use root@node0 admin credentials; client queries use a named identity.
Recovery milestones are observed states, with uncertainty up to the polling interval.
"""
import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
MON_HOST = 'root@node0'
CEPH_NAME = 'client.' + socket.gethostname().split('.')[0]
SSH_OPTS = ['-o','BatchMode=yes','-o','ConnectTimeout=10']

def vlog(message, verbose, file=sys.stderr):
    if verbose: print(message,file=file)

def run_local(command, verbose=False):
    argv=shlex.split(command) if isinstance(command,str) else list(command)
    if argv[:2]==['sudo','ceph'] and '--name' not in argv:
        argv[1:2]=['-n','ceph','--name',CEPH_NAME]
    return subprocess.run(argv,capture_output=True,text=True,timeout=30)

def run_on_mon(command,verbose=False):
    return subprocess.run([*(['sudo','-n'] if os.geteuid()!=0 else []),'ssh',*SSH_OPTS,MON_HOST,command],capture_output=True,text=True,timeout=30)

def run_on_osd_host(host,command,verbose=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',host): raise ValueError('Invalid OSD hostname')
    return subprocess.run([*(['sudo','-n'] if os.geteuid()!=0 else []),'ssh',*SSH_OPTS,'root@'+host,command],capture_output=True,text=True,timeout=30)

def checked(result):
    if result.returncode: raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Command failed')
    return result.stdout

def find_osd_info(osd_id,verbose=False):
    info=json.loads(checked(run_local(['sudo','ceph','osd','find',str(osd_id),'-f','json'])))
    host=info.get('host') or info['crush_location']['host']
    fsid=checked(run_local(['sudo','ceph','fsid'])).strip()
    errors=[]
    for unit in [f'ceph-osd@{osd_id}.service',f'ceph-{fsid}@osd.{osd_id}.service']:
        result=run_on_osd_host(host,'systemctl show '+shlex.quote(unit)+' --property=LoadState --property=ActiveState')
        if result.returncode:errors.append(result.stderr.strip())
        if result.returncode==0 and 'LoadState=loaded' in result.stdout and 'ActiveState=active' in result.stdout:
            return host,unit
    raise RuntimeError(f'No loaded active OSD unit found for osd.{osd_id} on {host}; SSH errors: {errors}')

def stop_osd_daemon(osd_id,host,unit,verbose=False):
    checked(run_on_osd_host(host,'systemctl stop '+shlex.quote(unit)))
    state=checked(run_on_osd_host(host,'systemctl show '+shlex.quote(unit)+' --property=ActiveState --value')).strip()
    if state not in ('inactive','failed'): raise RuntimeError('OSD unit did not stop: '+state)

def start_osd_daemon(host,unit,verbose=False):
    checked(run_on_osd_host(host,'systemctl start '+shlex.quote(unit)))
    checked(run_on_osd_host(host,'systemctl is-active '+shlex.quote(unit)))

def osd_in(osd_id,verbose=False): checked(run_on_mon('ceph osd in '+str(int(osd_id))))
def osd_out(osd_id,verbose=False): checked(run_on_mon('ceph osd out '+str(int(osd_id))))

def recovery_snapshot(osd_id):
    status=json.loads(checked(run_local(['sudo','ceph','status','-f','json'])))
    dump=json.loads(checked(run_local(['sudo','ceph','osd','dump','-f','json'])))
    target=next(o for o in dump['osds'] if o['osd']==osd_id)
    pg=status.get('pgmap',{}); states=pg.get('pgs_by_state',[])
    total=pg.get('num_pgs',0)
    clean=sum(s['count'] for s in states if s['state_name']=='active+clean')
    return {'observed_utc':datetime.now(timezone.utc).isoformat(),'osd':target,
            'pgmap':pg,'all_clean':total>0 and clean==total,
            'degraded':any('degraded' in s['state_name'] for s in states),
            'recovering':any('recover' in s['state_name'] or 'backfill' in s['state_name'] for s in states)}

def watch_recovery(osd_id,timeout,interval=2.0,raw_path=None,require_restored=False,stop_event=None):
    """Starts only after fault/restore request; no historical journal events.
    Completion requires target down/out for remapping or up/in for restoration,
    plus all PGs clean. Clean-before-markout cannot end the fault observation.
    """
    deadline=time.monotonic()+timeout
    events={k:None for k in ('down','marked_out','degraded','recovering','healthy')}
    raw=open(raw_path,'a') if raw_path else None
    try:
        while time.monotonic()<deadline and not (stop_event and stop_event.is_set()):
            s=recovery_snapshot(osd_id); stamp=s['observed_utc']; o=s['osd']
            if raw: raw.write(json.dumps(s)+'\n');raw.flush()
            for key,condition in [('down',not o['up']),('marked_out',not o['in']),('degraded',s['degraded']),('recovering',s['recovering'])]:
                if condition and events[key] is None: events[key]=stamp
            target_ok=(o['up'] and o['in']) if require_restored else (not o['up'] and not o['in'] and events['down'] is not None)
            if target_ok and s['all_clean']:
                events['healthy']=stamp
                return {'events':events,'complete':True,'poll_interval_s':interval}
            if stop_event: stop_event.wait(interval)
            else: time.sleep(interval)
        return {'events':events,'complete':False,'poll_interval_s':interval}
    finally:
        if raw:raw.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('osd_id',type=int);p.add_argument('--confirm-fault',action='store_true',required=True)
    p.add_argument('--timeout',type=float,default=7200);p.add_argument('--poll-interval',type=float,default=2)
    p.add_argument('--no-restore',action='store_true');a=p.parse_args()
    if a.osd_id<0 or a.timeout<=0 or a.poll_interval<=0:p.error('Invalid OSD ID or timing')
    host,unit=find_osd_info(a.osd_id)
    baseline=recovery_snapshot(a.osd_id)
    if not baseline['all_clean'] or not baseline['osd']['up'] or not baseline['osd']['in']:raise RuntimeError('Require clean cluster and up/in target')
    attempted=False
    try:
        attempted=True;stop_osd_daemon(a.osd_id,host,unit)
        print(json.dumps(watch_recovery(a.osd_id,a.timeout,a.poll_interval,raw_path='recovery-events.jsonl'),indent=2))
    finally:
        if attempted and not a.no_restore:
            start_osd_daemon(host,unit);osd_in(a.osd_id)
            restored=watch_recovery(a.osd_id,a.timeout,a.poll_interval,raw_path='restoration-events.jsonl',require_restored=True)
            if not restored['complete']:raise RuntimeError('Restoration timed out')
if __name__=='__main__':main()
