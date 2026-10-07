import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).parent.parent/'scripts'))
import run_recovery_experiment as runner
import measure_ceph_recovery as recovery

class RecoveryTests(unittest.TestCase):
    def test_clean_before_markout_does_not_finish(self):
        samples=[{'observed_utc':'first','osd':{'up':0,'in':1},'all_clean':True,'degraded':False,'recovering':False},
                 {'observed_utc':'second','osd':{'up':0,'in':0},'all_clean':True,'degraded':False,'recovering':False}]
        with patch.object(recovery,'recovery_snapshot',side_effect=samples):
            r=recovery.watch_recovery(3,1,interval=.001)
        self.assertTrue(r['complete']);self.assertEqual(r['events']['healthy'],'second')
    def test_restore_requires_up_in(self):
        sample={'observed_utc':'now','osd':{'up':0,'in':0},'all_clean':True,'degraded':False,'recovering':False}
        with patch.object(recovery,'recovery_snapshot',return_value=sample):
            r=recovery.watch_recovery(3,.005,interval=.001,require_restored=True)
        self.assertFalse(r['complete'])
    def test_poll_failure_is_not_zero_or_healthy(self):
        with patch.object(recovery,'recovery_snapshot',side_effect=RuntimeError('auth failure')):
            with self.assertRaisesRegex(RuntimeError,'auth failure'):recovery.watch_recovery(3,1)
    def test_discovers_package_unit(self):
        replies=[subprocess.CompletedProcess([],0,'{"host":"node1"}',''),subprocess.CompletedProcess([],0,'abc','')]
        with patch.object(recovery,'run_local',side_effect=replies),patch.object(recovery,'run_on_osd_host',return_value=subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=active','')):
            self.assertEqual(recovery.find_osd_info(0),('node1','ceph-osd@0.service'))
    def test_auth_identity_is_explicit(self):
        with patch.object(recovery.subprocess,'run') as run:
            recovery.run_local(['sudo','ceph','status'])
        self.assertIn(recovery.CEPH_NAME,run.call_args.args[0]);self.assertIn('--name',run.call_args.args[0])
    def test_missing_disk_counters_fail(self):
        with patch.object(runner.psutil,'disk_io_counters',return_value={}):
            with self.assertRaises(RuntimeError):runner.disk_counters('rbd99')

class RunnerTests(unittest.TestCase):
    def exercise(self,fault=False,delay=.5,stop_error=None,app_exit=0,monitor_error=False,bp_count=2):
        real_popen=subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            output=str(Path(directory)/'analysis.bp')
            args=['--output',output,'--output-dir',directory,'--expected-steps','2','--fault-delay',str(delay)]
            args=(['0','--confirm-fault'] if fault else ['--no-fault'])+args
            a=runner.parse_args(args)
            with open(output+'_throughput.csv','w') as f:f.write('step,global_data_read_bytes\n1,4\n2,4\n')
            def monitor(a,device,proc,stop,path,phase,errors,ready):
                Path(path).write_text('timestamp,io_read_mb\n')
                if monitor_error:errors.append('Missing device counters')
                ready.set();stop.wait()
            def launch(*args,**kwargs):
                return real_popen([sys.executable,'-c',f'import time,sys; time.sleep(.12);sys.exit({app_exit})'],stdout=kwargs['stdout'],stderr=kwargs['stderr'],start_new_session=True)
            with patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')),patch.object(runner,'preflight',return_value={'mapping':{'device':'/dev/rbd77'}}),patch.object(runner,'metadata',return_value={}),patch.object(runner,'perf_monitor',side_effect=monitor),patch.object(runner.subprocess,'Popen',side_effect=launch),patch.object(runner,'bp_steps',side_effect=lambda *x:iter([('U/pdf',bp_count),('V/pdf',bp_count)])),patch.object(runner,'checked',return_value='0.5'),patch.object(runner,'status',return_value={'health':{'status':'HEALTH_OK'}}),patch.object(recovery,'find_osd_info',return_value=('node1','ceph-osd@0.service')),patch.object(recovery,'recovery_snapshot',return_value={'osd':{'up':1,'in':1}}),patch.object(recovery,'stop_osd_daemon',side_effect=stop_error) as stop,patch.object(recovery,'start_osd_daemon') as start,patch.object(recovery,'osd_in') as mark_in,patch.object(recovery,'watch_recovery',return_value={'complete':True,'events':{}}):
                rc=runner.run(a)
            result=json.loads(next(Path(directory).glob('recovery-experiment-*/results.json')).read_text())
            return rc,result,stop.call_count,start.call_count,mark_in.call_count
    def test_no_fault_does_not_touch_osds(self):
        rc,r,st,sa,mi=self.exercise()
        self.assertEqual(rc,0);self.assertEqual((st,sa,mi),(0,0,0));self.assertEqual(r['logical_input_bytes'],8)
    def test_finished_analysis_skips_late_fault(self):
        rc,r,st,_,_=self.exercise(fault=True,delay=.8)
        self.assertEqual(rc,1);self.assertEqual(st,0)
        self.assertLess(r['duration_analysis_s'],.6)
        self.assertEqual(r['fault_injection_status'],'skipped_analysis_finished_before_delay')
    def test_partial_stop_failure_restores(self):
        rc,r,st,sa,mi=self.exercise(fault=True,delay=.01,stop_error=RuntimeError('verification failed'))
        self.assertEqual(rc,1);self.assertEqual((st,sa,mi),(1,1,1));self.assertIn('restoration',r)
    def test_mpi_failure_invalidates_trial(self):
        rc,r,*_=self.exercise(app_exit=2)
        self.assertEqual(rc,1);self.assertEqual(r['analysis_exit_code'],2);self.assertEqual(r['trial_status'],'failed')
    def test_monitor_failure_invalidates_trial(self):
        rc,r,*_=self.exercise(monitor_error=True)
        self.assertEqual(rc,1);self.assertIn('monitor_errors',r)
    def test_output_step_count_failure(self):
        rc,r,*_=self.exercise(bp_count=1)
        self.assertEqual(rc,1);self.assertIn('step count mismatch',r['error'])
    def test_fault_needs_explicit_flag(self):
        with self.assertRaises(SystemExit):runner.parse_args(['0','--output','x','--expected-steps','2'])

if __name__=='__main__':unittest.main()
