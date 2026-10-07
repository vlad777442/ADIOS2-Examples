#!/usr/bin/env python3
"""Run and validate an MPI Gray-Scott analysis with optional authorized OSD fault.

Baseline: --no-fault --expected-steps 3600 --input ... --output ...
Fault: OSD_ID --confirm-fault --expected-steps 3600 --input ... --output ...
The confirmation flag is deliberate: default invocation cannot stop an OSD.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime,timezone
import psutil
import measure_ceph_recovery as recovery

GS_DIR=Path(__file__).resolve().parent.parent
ADIOS2_LIB='/users/vlad777/software/adios2/lib'
MIB=1024**2

def utc():return datetime.now(timezone.utc).isoformat()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def checked(argv,timeout=30):
    r=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(f'{argv[0]} failed: {r.stderr.strip()}')
    return r.stdout

def ceph(*args):return ['sudo','-n','ceph','--name',recovery.CEPH_NAME,*args]
def status():return json.loads(checked(ceph('status','-f','json')))

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('osd_id',type=int,nargs='?')
    p.add_argument('--no-fault',action='store_true')
    p.add_argument('--confirm-fault',action='store_true')
    p.add_argument('--no-restore',action='store_true')
    p.add_argument('--mpi-procs',type=int,default=8)
    p.add_argument('--bins',type=int,default=100)
    p.add_argument('--fault-delay',type=float,default=280)
    p.add_argument('--timeout',type=float,default=7200,help='Recovery deadline after fault request, seconds')
    p.add_argument('--analysis-timeout',type=float,default=14400)
    p.add_argument('--monitor-interval',type=float,default=3)
    p.add_argument('--poll-interval',type=float,default=2)
    p.add_argument('--input',default='/mnt/grayscott/gray-scott/gs-900g.bp')
    p.add_argument('--output',required=True)
    p.add_argument('--output-dir',default=str(GS_DIR/'results'))
    p.add_argument('--rbd-mount',default='/mnt/grayscott')
    p.add_argument('--network-interface',default='enp6s0f1')
    p.add_argument('--expected-steps',type=int,required=True)
    p.add_argument('--expected-data-pool',default='grayscott-900g')
    p.add_argument('--ceph-name',default=recovery.CEPH_NAME)
    p.add_argument('--rbd-name',default='client.grayscott-'+socket.gethostname().split('.')[0])
    p.add_argument('--cache-policy',default='natural',choices=['natural','warm'])
    p.add_argument('--trial-id',default='')
    p.add_argument('--start-at',type=float,help='UTC epoch barrier for concurrent jobs; clocks must be synchronized')
    p.add_argument('--binary',default=str(GS_DIR/'build-grayscott/adios2-pdf-calc'))
    p.add_argument('--bpls',default='/users/vlad777/software/adios2/bin/bpls')
    p.add_argument('--collect-osd-perf',action='store_true',help='Record cluster-wide OSD latency snapshots')
    p.add_argument('--verbose','-v',action='store_true')
    a=p.parse_args(argv)
    if not a.no_fault and (a.osd_id is None or not a.confirm_fault):p.error('Use --no-fault, or supply OSD_ID and --confirm-fault after explicit authorization')
    if a.no_fault and (a.confirm_fault or a.no_restore):p.error('Fault-only flags cannot accompany --no-fault')
    if a.osd_id is not None and a.osd_id<0:p.error('OSD ID must be nonnegative')
    for key in ['mpi_procs','bins','timeout','analysis_timeout','monitor_interval','poll_interval','expected_steps']:
        if getattr(a,key)<=0:p.error(key+' must be positive')
    if a.fault_delay<0:p.error('fault-delay must be nonnegative')
    return a

def bp_steps(path,bpls,fields):
    listing=checked([bpls,str(path)],timeout=120)
    for field in fields:
        match=re.search(r'(?m)^\s*\w+\s+'+re.escape(field)+r'\s+(\d+)\*',listing)
        if not match:raise RuntimeError(f'Missing step-counted variable {field} in {path}')
        yield field,int(match.group(1))

def preflight(a):
    cluster=status()
    if cluster['health']['status']!='HEALTH_OK':raise RuntimeError('Require HEALTH_OK baseline')
    pg=cluster['pgmap']; states=pg.get('pgs_by_state',[])
    if sum(s['count'] for s in states if s['state_name']=='active+clean')!=pg['num_pgs']:raise RuntimeError('Require all PGs active+clean')
    mount=Path(a.rbd_mount).resolve()
    info=json.loads(checked(['findmnt','-J','-M',str(mount),'-o','SOURCE,TARGET,FSTYPE']))['filesystems'][0]
    source=os.path.realpath(info['source'])
    if not re.fullmatch(r'/dev/rbd\d+',source):raise RuntimeError('Mount must resolve to a verified whole RBD device, got '+source)
    mappings=json.loads(checked(['sudo','-n','rbd','device','list','--format','json']))
    mappings=list(mappings.values()) if isinstance(mappings,dict) else mappings
    mapping=next((m for m in mappings if os.path.realpath(m['device'])==source),None)
    if mapping is None:raise RuntimeError('Mounted device absent from RBD mappings')
    image=json.loads(checked(['sudo','-n','rbd','--name',a.rbd_name,
                             'info',mapping['pool']+'/'+mapping['name'],'--format','json']))
    data_pool=image.get('data_pool')
    if data_pool!=a.expected_data_pool:raise RuntimeError(f'Image data pool {data_pool} differs from expected {a.expected_data_pool}')
    for path in [a.input,a.output]:
        if not Path(path).resolve().is_relative_to(mount):raise RuntimeError('Input/output must be under the verified RBD mount')
    if Path(a.output+'_throughput.csv').exists():raise RuntimeError('Refusing existing throughput CSV')
    if Path(a.output).exists():raise RuntimeError('Refusing existing output: '+a.output)
    if Path(a.input).resolve()==Path(a.output).resolve():raise RuntimeError('Input and output coincide')
    if a.mpi_procs>(psutil.cpu_count(logical=False) or 1):raise RuntimeError('MPI ranks exceed physical core count')
    if shutil.disk_usage(mount).free<1024**3:raise RuntimeError('Require at least 1 GiB free for output')
    if not os.access(a.binary,os.X_OK):raise RuntimeError('Analysis executable unavailable')
    interface=checked(['ip','-br','addr','show','dev',a.network_interface]).strip()
    if not interface:raise RuntimeError('Missing network interface')
    counts=dict(bp_steps(a.input,a.bpls,['U','V']))
    if any(v!=a.expected_steps for v in counts.values()):raise RuntimeError('Input step count mismatch: '+str(counts))
    pool=json.loads(checked(ceph('osd','pool','get',a.expected_data_pool,'all','-f','json')))
    profile=pool.get('erasure_code_profile')
    ec_profile=json.loads(checked(ceph('osd','erasure-code-profile','get',profile,'-f','json'))) if profile else None
    rules=json.loads(checked(ceph('osd','crush','rule','dump','-f','json')))
    rule=next((r for r in rules if r['rule_name']==pool['crush_rule'] or r['rule_id']==pool['crush_rule']),None)
    if ec_profile is None or ec_profile.get('crush-failure-domain')!='osd':raise RuntimeError('Experimental profile must use OSD failure domain')
    if rule is None or any(step.get('type')!='osd' for step in rule['steps'] if step['op'].startswith('choose')):raise RuntimeError('CRUSH placement must select OSDs without host locality')
    return {'ceph':cluster,'mount':info,'mapping':mapping,'image':image,'interface':interface,
            'input_steps':counts,'pool':pool,'ec_profile':ec_profile,'crush_rules':rules}

def terminate_process(proc):
    if proc is not None and proc.poll() is None:
        os.killpg(proc.pid,signal.SIGTERM)
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=10)

def network_bytes(interface):
    counters=psutil.net_io_counters(pernic=True)
    if interface not in counters:raise RuntimeError('Network interface disappeared')
    c=counters[interface];return c.bytes_recv,c.bytes_sent

def disk_counters(device):
    c=psutil.disk_io_counters(perdisk=True,nowrap=True).get(device)
    if c is None:raise RuntimeError('RBD disk counters unavailable: '+device)
    return c

def perf_monitor(a,device,proc,stop,path,phase,errors,ready):
    columns=['timestamp','elapsed_s','phase','cpu_percent','memory_mb','rbd_usage_mb',
             'io_read_mb','io_write_mb','net_rx_mb','net_tx_mb','read_iops','write_iops',
             'read_latency_ms','write_latency_ms','analysis_rss_mib','analysis_cpu_percent']
    previous=None;start=time.monotonic();processes={}
    try:
        psutil.cpu_percent(interval=None)
        with open(path,'x',newline='') as f:
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
            while not stop.is_set():
                mono=time.monotonic();d=disk_counters(device);rx,tx=network_bytes(a.network_interface)
                row=dict.fromkeys(columns,'')
                row.update(timestamp=f'{time.time():.6f}',elapsed_s=mono-start,phase=phase[0],
                           cpu_percent=psutil.cpu_percent(interval=None),memory_mb=psutil.virtual_memory().used/MIB,
                           rbd_usage_mb=shutil.disk_usage(a.rbd_mount).used/MIB)
                if previous:
                    old,orx,otx,then=previous;dt=mono-then
                    values={'io_read_mb':(d.read_bytes-old.read_bytes)/MIB/dt,
                            'io_write_mb':(d.write_bytes-old.write_bytes)/MIB/dt,
                            'net_rx_mb':(rx-orx)/MIB/dt,'net_tx_mb':(tx-otx)/MIB/dt,
                            'read_iops':(d.read_count-old.read_count)/dt,'write_iops':(d.write_count-old.write_count)/dt}
                    if any(v<0 for v in values.values()):raise RuntimeError('Counter reset during monitoring')
                    row.update(values)
                    nr=d.read_count-old.read_count;nw=d.write_count-old.write_count
                    row['read_latency_ms']=(d.read_time-old.read_time)/nr if nr else ''
                    row['write_latency_ms']=(d.write_time-old.write_time)/nw if nw else ''
                previous=d,rx,tx,mono
                if proc[0] is not None and proc[0].poll() is None:
                    rss=cpu=0
                    try:
                        parent=psutil.Process(proc[0].pid)
                        for process in [parent,*parent.children(recursive=True)]:
                            key=(process.pid,process.create_time())
                            tracked=processes.setdefault(key,process)
                            rss+=tracked.memory_info().rss;cpu+=tracked.cpu_percent(interval=None)
                        row['analysis_rss_mib']=rss/MIB;row['analysis_cpu_percent']=cpu
                    except psutil.NoSuchProcess:pass
                w.writerow(row);f.flush();ready.set();stop.wait(a.monitor_interval)
    except Exception as e:errors.append(str(e));ready.set()

def metadata(a):
    return {'script_sha256':digest(__file__),'helper_sha256':digest(recovery.__file__),
            'binary_sha256':digest(a.binary),'mpi_version':checked(['mpirun','--version']),
            'adios2_version':checked([a.bpls,'-V']),
            'xml_sha256':digest(GS_DIR/'adios2.xml'),'cpu_affinity':sorted(os.sched_getaffinity(0)),
            'cpu_count_physical':psutil.cpu_count(logical=False),'cpu_count_logical':psutil.cpu_count(),
            'hostname':socket.gethostname(),'cache_policy':a.cache_policy,
            'cache_note':'No automatic cache dropping or warming; warm label requires an explicit prior full pass.'}

def run(a):
    recovery.CEPH_NAME=a.ceph_name
    baseline=preflight(a)
    host=unit=None
    if not a.no_fault:
        target=recovery.recovery_snapshot(a.osd_id)['osd']
        if not target['up'] or not target['in']:raise RuntimeError('Target must start up/in')
        host,unit=recovery.find_osd_info(a.osd_id)
    out=Path(a.output_dir)/('recovery-experiment-'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:8])
    out.mkdir(parents=True,exist_ok=False)
    result={'runner_pid':os.getpid(),'schema_version':2,'trial_status':'running','args':vars(a),'baseline':baseline,
            'no_fault':a.no_fault,'hostname':socket.gethostname(),'mpi_procs':a.mpi_procs,'bins':a.bins,
            'input':a.input,'output':a.output,'output_dir':str(out),'perf_csv':str(out/'perf.csv'),
            'pool_label':a.expected_data_pool,'metadata':metadata(a),'fault_injection_status':'not_requested' if a.no_fault else 'not_attempted'}
    (out/'results.json').write_text(json.dumps(result,indent=2))
    stop=threading.Event();ready=threading.Event();errors=[];phase=['baseline'];proc=[None];monitor=None
    attempted=False;worker=None;worker_stop=threading.Event();worker_errors=[];osd_thread=None;completion_thread=None;completion=threading.Event()
    failure=None;returncode=1
    def interrupted(signum,frame):raise KeyboardInterrupt('Signal '+str(signum))
    old_handlers={s:signal.signal(s,interrupted) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
    try:
        monitor=threading.Thread(target=perf_monitor,args=(a,Path(baseline['mapping']['device']).name,proc,stop,out/'perf.csv',phase,errors,ready))
        monitor.start()
        if not ready.wait(30) or errors:raise RuntimeError('Monitor startup failed: '+str(errors))
        if a.collect_osd_perf:
            def collect_osd():
                try:
                    with open(out/'osd-perf.jsonl','x') as f:
                        while not stop.is_set():
                            snapshot={'utc':utc(),'phase':phase[0],'perf':json.loads(checked(ceph('osd','perf','-f','json')))}
                            f.write(json.dumps(snapshot)+'\n');f.flush()
                            stop.wait(max(5,a.monitor_interval))
                except Exception as e:errors.append('OSD collector: '+str(e))
            osd_thread=threading.Thread(target=collect_osd);osd_thread.start()
        if a.start_at:
            if a.start_at<time.time():raise RuntimeError('Scheduled start already passed')
            while time.time()<a.start_at:
                if errors:raise RuntimeError('Monitor failed: '+str(errors))
                time.sleep(min(.1,max(0,a.start_at-time.time())))
        Path(a.output).parent.mkdir(parents=True,exist_ok=True)
        env=os.environ.copy();env['LD_LIBRARY_PATH']=ADIOS2_LIB+':'+env.get('LD_LIBRARY_PATH','')
        cmd=['mpirun','--bind-to','core','--report-bindings','-n',str(a.mpi_procs),a.binary,a.input,a.output,str(a.bins)]
        if os.geteuid()==0:cmd.insert(1,'--allow-run-as-root')
        result['analysis_command']=cmd;phase[0]='analysis'
        with open(out/'analysis.log','x') as log:
            start=time.monotonic();result['t_analysis_start']=utc()
            proc[0]=subprocess.Popen(cmd,cwd=GS_DIR,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            def observe_completion():
                rc=proc[0].wait()
                result['duration_analysis_s']=time.monotonic()-start
                result['t_analysis_end']=utc();result['analysis_exit_code']=rc
                completion.set()
            completion_thread=threading.Thread(target=observe_completion)
            completion_thread.start()
            next_osd=0
            while True:
                if completion.wait(.1):
                    rc=result['analysis_exit_code'];break
                elapsed=time.monotonic()-start
                if elapsed>a.analysis_timeout:raise TimeoutError('Analysis timed out')
                if errors or worker_errors:raise RuntimeError('Collector failed: '+str(errors+worker_errors))
                if not a.no_fault and not attempted and elapsed>=a.fault_delay:
                    if proc[0].poll() is not None:continue
                    result['t_fault_requested']=utc();attempted=True
                    recovery.stop_osd_daemon(a.osd_id,host,unit)
                    result['t_fault_injected']=utc();result['fault_injection_status']='confirmed_service_stopped'
                    phase[0]='fault_recovery'
                    def observe():
                        try:result['recovery']=recovery.watch_recovery(a.osd_id,a.timeout,a.poll_interval,str(out/'recovery-events.jsonl'),stop_event=worker_stop)
                        except Exception as e:worker_errors.append(str(e))
                    worker=threading.Thread(target=observe);worker.start()
            if rc:raise RuntimeError('MPI analysis failed: '+str(rc))
        phase[0]='recovery_wait' if attempted else 'validation'
        if not a.no_fault and not attempted:
            result['fault_injection_status']='skipped_analysis_finished_before_delay'
            raise RuntimeError('Requested fault did not occur while analysis ran; trial invalid')
        if worker:
            worker.join(timeout=a.timeout+35)
            if worker.is_alive() or worker_errors or not result.get('recovery',{}).get('complete'):raise RuntimeError('Fault recovery incomplete: '+str(worker_errors))
        output_counts=dict(bp_steps(a.output,a.bpls,['U/pdf','V/pdf']))
        result['validation']={'output_steps':output_counts,'expected_steps':a.expected_steps}
        if any(n!=a.expected_steps for n in output_counts.values()):raise RuntimeError('Output step count mismatch')
        # Read the final output step through ADIOS2, not just metadata.
        result['validation']['final_step_readback']=checked([a.bpls,'-d','-s',f'{a.expected_steps-1},0,0','-c','1,1,1',a.output,'U/pdf','V/pdf'],timeout=120)
        if re.search(r'(?i)\b(?:nan|inf|infinity)\b',result['validation']['final_step_readback']):raise RuntimeError('Nonfinite final output sample')
        source=Path(a.output+'_throughput.csv')
        if not source.is_file():raise RuntimeError('Application throughput CSV missing')
        destination=out/source.name;shutil.copy2(source,destination);result['throughput_csv']=str(destination)
        with source.open() as f:rows=list(csv.DictReader(f))
        if len(rows)!=a.expected_steps:raise RuntimeError('Throughput CSV step count mismatch')
        input_bytes=sum(int(row['global_data_read_bytes']) for row in rows)
        result['logical_input_bytes']=round(input_bytes)
        result['logical_throughput_mib_s']=input_bytes/MIB/result['duration_analysis_s']
        result['throughput_scope']='global bytes summed across MPI ranks; job wall time includes launch and close'
        result['trial_status']='complete';returncode=0
    except (Exception,KeyboardInterrupt) as e:
        failure=str(e);result['trial_status']='interrupted' if isinstance(e,KeyboardInterrupt) else 'failed';result['error']=failure
    finally:
        # Do not let a repeated SIGTERM interrupt restoration/recording.
        for s in old_handlers:signal.signal(s,signal.SIG_IGN)
        try:terminate_process(proc[0])
        except Exception as e:result['process_cleanup_error']=str(e);returncode=1
        worker_stop.set()
        if completion_thread:completion_thread.join(timeout=15)
        if worker:worker.join(timeout=35)
        if worker and worker.is_alive():result['cleanup_worker_error']='Recovery observer did not stop';returncode=1
        if attempted and not a.no_restore:
            phase[0]='restoration';result['t_restore_requested']=utc()
            try:
                recovery.start_osd_daemon(host,unit);recovery.osd_in(a.osd_id)
                result['restoration']=recovery.watch_recovery(a.osd_id,a.timeout,a.poll_interval,str(out/'restoration-events.jsonl'),require_restored=True)
                if not result['restoration']['complete']:raise RuntimeError('Restoration timed out')
            except Exception as e:result['restoration_error']=str(e);returncode=1
        stop.set()
        if monitor:monitor.join(timeout=10)
        if osd_thread:osd_thread.join(timeout=35)
        if osd_thread and osd_thread.is_alive():errors.append('OSD collector failed to stop')
        if errors or (monitor and monitor.is_alive()):result['monitor_errors']=errors or ['Monitor failed to stop'];returncode=1
        try:
            result['final_ceph']=status()
            if not a.no_restore and result['final_ceph']['health']['status']!='HEALTH_OK':raise RuntimeError('Final cluster is not HEALTH_OK')
        except Exception as e:result['final_ceph_error']=str(e);returncode=1
        if returncode and result['trial_status']=='complete':result['trial_status']='failed'
        result['finished_utc']=utc();result['exit_code']=returncode
        result['duration_fault_delay_s']=a.fault_delay if not a.no_fault else None
        result['osd_id']=a.osd_id
        events=result.get('recovery',{}).get('events',{})
        for event,stamp in events.items():result['t_'+event]=stamp
        def seconds(first,last):
            if not first or not last:return None
            value=(datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds()
            return value if value>=0 else None
        result['duration_grace_period_s']=seconds(events.get('down'),events.get('marked_out'))
        result['duration_out_to_recovering_s']=seconds(events.get('marked_out'),events.get('recovering'))
        result['duration_recovery_s']=seconds(events.get('recovering'),events.get('healthy'))
        result['duration_total_downtime_s']=seconds(result.get('t_fault_requested'),events.get('healthy'))
        result['recovery_timing_note']='Observed JSON state transitions; polling uncertainty up to poll interval plus command latency. Healthy means PG remapping completed; restoration recorded separately.'

        result['metric_units']={'memory_mb':'MiB','rbd_usage_mb':'MiB','io_read_mb':'MiB/s','io_write_mb':'MiB/s','net_rx_mb':'MiB/s','net_tx_mb':'MiB/s'}
        temporary=out/'results.json.tmp';temporary.write_text(json.dumps(result,indent=2));temporary.replace(out/'results.json')
        for s,h in old_handlers.items():signal.signal(s,h)
    print('Results: '+str(out));print('Trial status: '+result['trial_status'])
    plot=GS_DIR/'scripts/plot_nofault_io.py'
    if a.no_fault and plot.is_file():
        result['duration_analysis_s']=result.get('duration_analysis_s',0)
        try:
            plotting=subprocess.run([sys.executable,str(plot),str(out/'perf.csv'),str(out/'results.json'),str(out/'nofault_io.png')],timeout=120)
            result['plot_exit_code']=plotting.returncode
        except Exception as e:result['plot_error']=str(e)
        (out/'results.json').write_text(json.dumps(result,indent=2))
    return returncode

def main():
    try:return run(parse_args())
    except Exception as e:print('ERROR: '+str(e),file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
