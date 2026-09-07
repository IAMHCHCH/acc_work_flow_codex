"""Durable workflow for the agent already running in the user's client.

The model chooses and executes work; this module owns evidence and completion
gates. It does not spawn a second model or assert that a CLI return equals success.
"""
import copy
import os
from pathlib import Path
import subprocess
import uuid

from .cases import Library
from .io import FlowError, digest, execute, file_hash, git, inside, now, read_json, write_json
from .runner import Runner, create_task, validate_report


def start_session(root, config, request, kind, repositories):
    task = create_task(root, config, request, kind, repositories)
    task.update(engine='native', stage='inspect', plan=None, checkpoints={}, attachments=[],
                status='active', needs_repair=False, client='current-agent')
    directory = Path(root) / '.accflow/tasks' / task['id']
    write_json(directory / 'task.json', task)
    runner = Runner(root, task['id'])
    try:
        runner.prepare()
    except (FlowError, OSError) as error:
        runner.task['status'] = 'blocked'
        runner.save('Preparation failed: ' + str(error))
        raise
    return Session(root, task['id']).next()


class Session:
    def __init__(self, root, task_id):
        self.root = Path(root).resolve()
        self.directory = inside(self.root / '.accflow/tasks', task_id)
        self.path = self.directory / 'task.json'
        self.task = read_json(self.path)
        if self.task.get('engine') != 'native':
            raise FlowError('This command requires a native agent session')
        self.workspace = self.directory / 'workspace'
        self.library = Library(self.root / 'cases')

    def save(self, event):
        self.task['events'].append({'at': now(), 'event': event, 'attempt': self.task['attempt']})
        write_json(self.path, self.task)

    def mutable(self):
        if digest(self.task['config']) != self.task['config_sha256']:
            raise FlowError('Frozen environment changed; start a follow-up task')
        if self.task['plan'] and digest(self.task['plan']) != self.task['plan_sha256']:
            raise FlowError('Fixed acceptance plan changed')
        if self.task['status'] == 'completed':
            raise FlowError('Completed session is immutable; start a follow-up task')
        if self.task['status'] == 'running':
            raise FlowError('Interrupted command: use session-recover before continuing')

    def snapshot(self):
        result = {}
        for name in self.task['commits']:
            repo = self.workspace / name
            paths = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z',
                                             '--cached', '--others', '--exclude-standard']).decode().split('\0')
            files = {}
            for item in sorted(set(paths) - {''}):
                p = repo / item
                if p.is_symlink():
                    files[item] = {'link': os.readlink(str(p))}
                elif p.is_file():
                    files[item] = {'sha256': file_hash(p), 'mode': p.stat().st_mode & 0o777}
                else:
                    files[item] = None
            result[name] = {'head': git(repo, 'rev-parse', 'HEAD'), 'files': files}
        return digest(result)

    def next(self):
        task = self.task
        if task['status'] == 'completed':
            action = 'Completed: deliver report, evidence, patch and case IDs to the user.'
        elif task['status'] == 'running':
            action = 'Inspect the interrupted command and remote state, then session-recover.'
        elif task['needs_repair']:
            action = 'Investigate recorded failure; rollback any dirty deployment, then retry with a reason.'
        elif not task['plan']:
            action = 'Inspect cloned repositories and environment; write plan JSON and call plan. Do not ask the user to author task commands.'
        else:
            pending = [s for s in task['plan']['stages'] if s not in task['checkpoints']]
            action = ('Perform stage ' + pending[0] + ', record commands, then checkpoint it.' if pending else
                      'Review acceptance evidence and relevant old cases; finish with structured review JSON and a report file.')
        return {'id': task['id'], 'status': task['status'], 'attempt': task['attempt'], 'next': action,
                'request': task['request'], 'kind': task['kind'], 'directory': str(self.directory),
                'repositories': {n: str(self.workspace / n) for n in task['commits']},
                'environment': task['config']['profile']['environment'],
                'plan': task['plan'], 'checkpoints': task['checkpoints'], 'failures': task['failures'],
                'records': task['records'], 'attachments': task['attachments'],
                'prior_cases': self.library.search(task['request'] + ' ' + ' '.join(task['commits']), 8)}

    def plan(self, value):
        self.mutable()
        if self.task['plan'] is not None:
            raise FlowError('Plan is fixed for this session; use a follow-up for scope changes')
        stages = value.get('stages')
        canonical = ['inspect', 'baseline', 'change', 'build', 'deploy', 'verify']
        if not isinstance(stages, list) or stages != [s for s in canonical if s in stages]:
            raise FlowError('stages must be unique and in inspect/baseline/change/build/deploy/verify order')
        required = ['inspect', 'verify'] + (['baseline', 'change', 'build'] if self.task['kind'] != 'analysis' else [])
        if not all(s in stages for s in required):
            raise FlowError('Plan is missing required stages: ' + ', '.join(required))
        for skipped in set(canonical) - set(stages):
            if not str(value.get('skip_reasons', {}).get(skipped, '')).strip():
                raise FlowError('Explain skipped stage: ' + skipped)
        checks = value.get('acceptance')
        if not isinstance(checks, list) or not checks:
            raise FlowError('Plan needs acceptance checks with id and description')
        ids = []
        for check in checks:
            if not isinstance(check.get('id'), str) or not check['id'].strip() or not str(check.get('description', '')).strip():
                raise FlowError('Every check needs an id and description')
            ids.append(check['id'])
        if len(set(ids)) != len(ids):
            raise FlowError('Duplicate acceptance check id')
        if 'deploy' in stages and not value.get('rollback'):
            raise FlowError('Deployment needs concrete rollback argv commands')
        for cmd in value.get('rollback', []):
            if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
                raise FlowError('rollback must contain non-empty argv arrays')
        self.task['plan'] = copy.deepcopy(value)
        self.task['plan_sha256'] = digest(value)
        self.save('Acceptance plan fixed by current agent')
        return self.next()

    def command(self, stage, argv, cwd=None, check=None, expect=0, contains=None, timeout=600):
        self.mutable()
        if stage not in ['inspect', 'baseline', 'build', 'deploy', 'verify', 'rollback'] or not argv:
            raise FlowError('Invalid execution stage or empty command')
        plan = self.task['plan']
        if stage != 'inspect' and (not plan or (stage != 'rollback' and stage not in plan['stages'])):
            raise FlowError('Fix the acceptance plan before executing this stage')
        if self.task['needs_repair'] and stage not in ['inspect', 'rollback']:
            raise FlowError('Failure requires diagnosis and retry before more execution')
        if stage == 'verify' and check not in [c['id'] for c in plan['acceptance']]:
            raise FlowError('Verification must identify a planned acceptance check')
        if expect != 0 and (stage != 'baseline' or expect in [124, 126, 127] or not contains):
            raise FlowError('Only baseline may expect failure; provide a diagnostic contains marker')
        if stage == 'deploy':
            if not self.task['config']['profile'].get('deployment_authorized'):
                raise FlowError('Deployment authority missing in environment; use existing user authorization or request the specific missing action')
            self.task['deployment_dirty'] = True
        if stage != 'inspect' and stage != 'rollback':
            preceding = plan['stages'][:plan['stages'].index(stage)]
            if any(s not in self.task['checkpoints'] for s in preceding):
                raise FlowError('Complete prior stage checkpoints first')
        if stage == 'rollback' and (not plan or argv not in plan.get('rollback', [])):
            raise FlowError('Use a rollback command from the fixed plan')
        directory = Path(cwd).resolve() if cwd else self.workspace
        log = self.directory / 'evidence' / ('{:04d}-{}-{}.log'.format(len(self.task['records']), stage, self.task['attempt']))
        before = self.snapshot() if stage == 'verify' else None
        self.task['status'] = 'running'
        self.task['inflight'] = {'stage': stage, 'argv': argv, 'log': str(log)}
        self.save('Command started')
        record = execute(argv, directory, log, timeout)
        output = log.read_text(encoding='utf-8', errors='replace')
        passed = record['returncode'] == expect and (not contains or contains in output)
        after = self.snapshot() if stage in ['build', 'verify'] else None
        if stage == 'verify' and before != after:
            passed = False
        record.update(stage=stage, attempt=self.task['attempt'], check=check, expected=expect,
                      contains=contains, passed=passed, source_sha256=after)
        self.task['records'].append(record)
        self.task.pop('inflight', None)
        self.task['status'] = 'active' if passed else 'blocked'
        if not passed:
            self.task['needs_repair'] = True
            self.task['failures'].append({'stage': stage, 'attempt': self.task['attempt'],
                                         'log': str(log), 'returncode': record['returncode'], 'at': now()})
        if stage == 'rollback' and passed:
            self.task.setdefault('rollback_done', []).append(argv)
            if all(c in self.task['rollback_done'] for c in plan['rollback']):
                self.task['deployment_dirty'] = False
        self.save('Command passed' if passed else 'Command failed; evidence retained')
        return record

    def attach(self, path, kind):
        self.mutable()
        source = Path(path).resolve()
        if kind not in ['artifact', 'report', 'supporting'] or not source.is_file():
            raise FlowError('Attachment needs an existing file and valid kind')
        destination = self.directory / 'attachments' / (uuid.uuid4().hex[:8] + '-' + source.name)
        destination.parent.mkdir(exist_ok=True)
        import shutil
        shutil.copyfile(source, destination)
        entry = {'kind': kind, 'path': str(destination), 'sha256': file_hash(destination),
                 'source': str(source), 'attempt': self.task['attempt']}
        self.task['attachments'].append(entry)
        self.save('Immutable attachment captured')
        return entry

    def checkpoint(self, stage, summary):
        self.mutable()
        plan = self.task['plan']
        if not plan or stage not in plan['stages'] or not summary.strip() or self.task['needs_repair']:
            raise FlowError('Checkpoint needs a plan, valid stage, summary and no unresolved failure')
        preceding = plan['stages'][:plan['stages'].index(stage)]
        if any(s not in self.task['checkpoints'] for s in preceding):
            raise FlowError('Complete earlier checkpoints first')
        current = [r for r in self.task['records'] if r['attempt'] == self.task['attempt'] and r['stage'] == stage and r.get('passed')]
        if stage != 'change' and not current:
            raise FlowError('Stage requires successful command evidence')
        if stage == 'build' and not any(a['kind'] == 'artifact' and a['attempt'] == self.task['attempt'] for a in self.task['attachments']):
            raise FlowError('Attach the build artifact before checkpointing build')
        if stage == 'build' and current[-1].get('source_sha256') != self.snapshot():
            raise FlowError('Source changed since the last build command; rebuild first')
        if stage == 'verify':
            self.validate_checks()
        self.task['checkpoints'][stage] = {'summary': summary, 'at': now(), 'attempt': self.task['attempt'],
                                           'source_sha256': self.snapshot()}
        self.save('Checkpoint ' + stage)
        return self.next()

    def validate_checks(self):
        snapshot = self.snapshot()
        if 'build' in self.task['plan']['stages']:
            built = self.task['checkpoints'].get('build', {})
            if built.get('source_sha256') != snapshot:
                raise FlowError('Source changed after build; rebuild before verification')
        records = [r for r in self.task['records'] if r['attempt'] == self.task['attempt'] and r['stage'] == 'verify']
        for check in self.task['plan']['acceptance']:
            matches = [r for r in records if r.get('check') == check['id']]
            if not matches or not matches[-1].get('passed') or matches[-1]['source_sha256'] != snapshot:
                raise FlowError('Missing, failed or stale verification for ' + check['id'])
        for item in self.task['attachments']:
            if item['kind'] == 'artifact' and item['attempt'] == self.task['attempt']:
                if not Path(item['source']).is_file() or file_hash(item['source']) != item['sha256']:
                    raise FlowError('Build artifact changed after capture: ' + item['source'])

    def retry(self, reason):
        self.mutable()
        if not reason.strip() or self.task['deployment_dirty']:
            raise FlowError('Retry needs a reason and completed rollback for dirty deployment')
        if self.task['attempt'] >= self.task['config'].get('max_attempts', 3):
            raise FlowError('Retry budget exhausted; report blocker with evidence')
        self.task['attempt'] += 1
        self.task['needs_repair'] = False
        self.task['status'] = 'active'
        self.task['checkpoints'] = {k:v for k,v in self.task['checkpoints'].items() if k in ['inspect','baseline']}
        self.task['rollback_done'] = []
        self.save('Retry: ' + reason)
        return self.next()

    def recover(self, reason):
        if self.task['status'] != 'running' or not reason.strip():
            raise FlowError('Recovery requires an interrupted session and checked remote-state explanation')
        self.task['failures'].append(dict(self.task['inflight'], reason=reason, attempt=self.task['attempt']))
        self.task['status'] = 'blocked'
        self.task['needs_repair'] = True
        self.task.pop('inflight', None)
        self.save('Interruption recorded; inspect and rollback before retry')
        return self.next()

    def finish(self, review, report):
        self.mutable()
        if self.task['needs_repair'] or not self.task['plan']:
            raise FlowError('Resolve failures and complete the plan first')
        if digest(self.task['plan']) != self.task['plan_sha256']:
            raise FlowError('Acceptance plan changed')
        if any(s not in self.task['checkpoints'] for s in self.task['plan']['stages']):
            raise FlowError('Missing stage checkpoints')
        self.validate_checks()
        validate_report(review)
        if review['status'] != 'ready':
            raise FlowError('Review is not ready')
        for item in self.task['records'] + self.task['attachments']:
            path = item.get('log', item.get('path'))
            if file_hash(path) != item['sha256']:
                raise FlowError('Evidence was modified: ' + path)
        valid_logs = [r['log'] for r in self.task['records'] if r['attempt'] == self.task['attempt'] and r['stage'] == 'verify' and r.get('passed')]
        for item in review['findings'] + review['revisions']:
            if not item['evidence'] or not set(item['evidence']).issubset(set(valid_logs)):
                raise FlowError('Findings and revisions must cite current successful verification logs')
        attached = self.attach(report, 'report')
        runner = Runner(self.root, self.task['id'])
        patches = runner.patches()
        self.task['patches'] = patches
        self.task['review_report'] = review
        self.task['report'] = attached['path']
        self.task['maintenance'] = self.library.archive(self.task, review, valid_logs, patches)
        self.task['case_ids'] = [k for k,v in self.library.load()['cases'].items() if self.task['id'] in v['occurrences']]
        self.task.update(status='completed', stage='done', deployment_dirty=False)
        self.save('Verification, review and case maintenance completed')
        return {'id': self.task['id'], 'status': 'completed', 'report': attached['path'],
                'case_ids': self.task['case_ids'], 'maintenance': self.task['maintenance'], 'patches': patches}
