import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from accflow.io import FlowError, git, read_json, write_json
from accflow.install import install_skills
from accflow.session import Session, start_session

ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'value.txt').write_text('broken')
        git(self.source, 'init'); git(self.source, 'add', '.')
        git(self.source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
        self.config = {'repositories': {'fixture': {'path': str(self.source), 'ref': 'HEAD'}},
                       'max_attempts': 3, 'profile': {'name': 'local', 'deployment_authorized': False,
                       'environment': {'hardware': 'local-fixture', 'kernel_release': 'none'}}}

    def tearDown(self):
        self.temp.cleanup()

    def session(self, kind='analysis'):
        result = start_session(self.root, self.config, 'Check fixture state', kind, ['fixture'])
        return Session(self.root, result['id'])

    def configure(self, session, kind='analysis', checks=None):
        plan = {'stages': ['inspect','verify'], 'skip_reasons': {
                s:'Not needed for this read-only analysis' for s in ['baseline','change','build','deploy']},
                'acceptance': checks or [{'id':'state','description':'The fixture is fixed'}], 'rollback': []}
        if kind != 'analysis':
            plan['stages'] = ['inspect','baseline','change','build','verify']
            plan['skip_reasons'] = {'deploy':'Local fixture only'}
        session.plan(plan)
        session.command('inspect', [sys.executable, '-c', 'print("INSPECT")'])
        session.checkpoint('inspect', 'Inspected source')

    def check(self, session, check='state', passing=True):
        return session.command('verify', [sys.executable, '-c', 'print("PASS"); raise SystemExit({})'.format(0 if passing else 1)], check=check, contains='PASS')

    def finish(self, session, revisions=None):
        record = [r for r in session.task['records'] if r['stage']=='verify' and r['attempt']==session.task['attempt']][-1]
        report = self.root / 'report.md'; report.write_text('Verified local fixture; no hardware claims.')
        finding = {'title':'Fixture checked', 'component':'fixture', 'symptom':'Uncertain state',
                   'root_cause':'Missing check', 'resolution':'Execute fixture check',
                   'lessons':['Local evidence only'], 'failed_attempts':[], 'evidence':[record['log']]}
        review = {'status':'ready','summary':'Checked','findings':[finding],'revisions':revisions or []}
        return session.finish(review, report)

    def test_analysis_needs_no_build_artifact_or_codex(self):
        s = self.session(); self.configure(s); self.check(s); s.checkpoint('verify','Passed')
        result = self.finish(s)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['maintenance']['active_count'], 1)
        self.assertEqual((self.source/'value.txt').read_text(), 'broken')

    def test_missing_check_blocks_completion(self):
        s=self.session(); self.configure(s, checks=[{'id':'a','description':'one'},{'id':'b','description':'two'}])
        self.check(s,'a')
        with self.assertRaises(FlowError): s.checkpoint('verify','Should fail')

    def test_failed_command_requires_retry_and_retains_history(self):
        s=self.session(); self.configure(s); self.check(s, passing=False)
        with self.assertRaises(FlowError): self.check(s)
        s.retry('Diagnosis complete')
        self.check(s); s.checkpoint('verify','Pass after repair'); self.finish(s)
        self.assertEqual(len(s.task['failures']),1)
        self.assertEqual(s.task['attempt'],2)

    def test_source_changed_after_verify_is_stale(self):
        s=self.session(); self.configure(s); self.check(s)
        (s.workspace/'fixture/value.txt').write_text('new source')
        with self.assertRaises(FlowError): s.checkpoint('verify','Stale')

    def test_verify_cannot_mutate_source(self):
        s=self.session(); self.configure(s)
        command=[sys.executable,'-c','from pathlib import Path; Path("fixture/value.txt").write_text("changed")']
        result=s.command('verify',command,check='state')
        self.assertFalse(result['passed'])

    def test_mutated_log_is_rejected(self):
        s=self.session(); self.configure(s); r=self.check(s); s.checkpoint('verify','Pass')
        Path(r['log']).write_text('fabricated')
        with self.assertRaises(FlowError): self.finish(s)

    def test_plan_cannot_drop_acceptance_checks(self):
        s=self.session(); self.configure(s)
        with self.assertRaises(FlowError): s.plan(s.task['plan'])

    def test_feature_requires_build_and_baseline(self):
        s=self.session('feature')
        with self.assertRaises(FlowError): self.configure(s)

    def test_bugfix_build_and_repair(self):
        s=self.session('bugfix'); self.configure(s,'bugfix')
        r=s.command('baseline',[sys.executable,'-c','print("REPRODUCED"); raise SystemExit(1)'],expect=1,contains='REPRODUCED')
        self.assertTrue(r['passed']); s.checkpoint('baseline','Reproduced')
        (s.workspace/'fixture/value.txt').write_text('fixed')
        s.checkpoint('change','Fixed')
        s.command('build',[sys.executable,'-c','from pathlib import Path; Path("artifact").write_text("built")'])
        with self.assertRaises(FlowError): s.checkpoint('build','Missing artifact')
        s.attach(s.workspace/'artifact','artifact'); s.checkpoint('build','Built')
        self.check(s); s.checkpoint('verify','Verified'); self.finish(s)
        self.assertGreater(s.task['patches']['fixture']['bytes'],0)

    def test_artifact_mutation_is_rejected(self):
        s=self.session(); self.configure(s)
        p=self.root/'artifact'; p.write_text('one'); s.attach(p,'artifact')
        self.check(s); p.write_text('two')
        with self.assertRaises(FlowError): s.checkpoint('verify','Changed artifact')

    def test_wrong_check_and_fake_baseline_failure_rejected(self):
        s=self.session(); self.configure(s)
        with self.assertRaises(FlowError): self.check(s,'unknown')
        with self.assertRaises(FlowError): s.command('verify',['false'],check='state',expect=1)

    def test_interruption_recovery_requires_new_verification(self):
        s=self.session(); self.configure(s); self.check(s)
        s.task.update(status='running',inflight={'stage':'verify','argv':['example']})
        s.save('Simulated interruption')
        with self.assertRaises(FlowError): s.checkpoint('verify','No')
        s.recover('Confirmed command stopped'); s.retry('Re-execute')
        with self.assertRaises(FlowError): s.checkpoint('verify','Old log')

    def test_dirty_deployment_blocks_retry(self):
        s=self.session(); self.configure(s); s.task['deployment_dirty']=True
        with self.assertRaises(FlowError): s.retry('Not rolled back')

    def test_rollback_without_plan_is_rejected(self):
        s=self.session()
        with self.assertRaises(FlowError): s.command('rollback', ['false'])

    def test_source_changed_after_build_requires_rebuild(self):
        s=self.session('feature'); self.configure(s,'feature')
        s.command('baseline',[sys.executable,'-c','print("BASELINE")'])
        s.checkpoint('baseline','Baseline recorded'); s.checkpoint('change','Changes ready')
        s.command('build',[sys.executable,'-c','from pathlib import Path; Path("artifact").write_text("built")'])
        s.attach(s.workspace/'artifact','artifact'); s.checkpoint('build','Built')
        (s.workspace/'fixture/value.txt').write_text('changed after build')
        with self.assertRaises(FlowError): s.checkpoint('build','Reuse old build log')
        self.check(s)
        with self.assertRaises(FlowError): s.checkpoint('verify','Old build')

    def test_failed_deployment_is_rolled_back_before_retry(self):
        self.config['profile']['deployment_authorized']=True
        s=self.session()
        rollback=[sys.executable,'-c','from pathlib import Path; Path("deployed").unlink()']
        s.plan({'stages':['inspect','deploy','verify'],
                'skip_reasons':{k:'Local deployment exercise' for k in ['baseline','change','build']},
                'acceptance':[{'id':'state','description':'Deployment fixture passes'}],
                'rollback':[rollback]})
        s.command('inspect',[sys.executable,'-c','print("INSPECT")']); s.checkpoint('inspect','Ready')
        r=s.command('deploy',[sys.executable,'-c','from pathlib import Path; Path("deployed").write_text("partial"); raise SystemExit(1)'])
        self.assertFalse(r['passed'])
        with self.assertRaises(FlowError): s.retry('Need rollback')
        s.command('rollback',rollback)
        self.assertFalse((s.workspace/'deployed').exists())
        self.assertFalse(s.task['deployment_dirty'])
        s.retry('Partial deployment removed')
        self.assertEqual(s.task['attempt'],2)

    def test_old_case_amended_and_history_preserved(self):
        first=self.session(); self.configure(first); self.check(first); first.checkpoint('verify','Pass')
        cid=self.finish(first)['case_ids'][0]
        second=self.session(); self.configure(second); r=self.check(second); second.checkpoint('verify','Pass')
        proposal={'case_id':cid,'action':'amend','reason':'New evidence narrows scope',
                  'resolution':'Scoped result','lessons':['Revised scope'],'evidence':[r['log']]}
        self.finish(second,[proposal])
        data=second.library.load()
        self.assertEqual(data['cases'][cid]['revision'],2)
        self.assertTrue(any(e['type']=='amend' and e['before']['revision']==1 for e in data['events']))

    def test_cli_start_and_exec_use_current_agent(self):
        write_json(self.root/'config.json',self.config)
        base=[sys.executable,str(ROOT/'workflow.py'),'--root',str(self.root)]
        started=json.loads(subprocess.check_output(base+['start','--request','inspect','--repos','fixture']))
        result=json.loads(subprocess.check_output(base+['exec',started['id'],'--stage','inspect','--',sys.executable,'-c','print("ACTUAL COMMAND")']))
        self.assertTrue(result['passed'])
        self.assertIn('ACTUAL COMMAND',Path(result['log']).read_text())


class InstallationTests(unittest.TestCase):
    def test_all_clients_install_and_run_from_unrelated_cwd(self):
        with tempfile.TemporaryDirectory(prefix='accflow space ') as temp:
            rows=install_skills(ROOT,'all',target=temp)
            self.assertEqual(len(rows),3)
            self.assertTrue(all(r['current'] for r in install_skills(ROOT,'all',target=temp,check=True)))
            for row in rows:
                output=subprocess.check_output([sys.executable,str(Path(row['path'])/'scripts/accflow.py'),'--help'],cwd='/tmp')
                self.assertIn(b'finish',output)

    def test_foreign_skill_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'.claude/skills/accflow'; path.mkdir(parents=True)
            (path/'SKILL.md').write_text('Someone else owns this')
            with self.assertRaises(FlowError): install_skills(ROOT,'claude',target=temp)


if __name__=='__main__': unittest.main()
