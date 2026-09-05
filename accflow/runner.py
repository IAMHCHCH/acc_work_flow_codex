import copy
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .cases import Library
from .io import FlowError, digest, execute, file_hash, git, inside, lock, now, read_json, write_json


STAGES = ['prepare', 'preflight', 'baseline', 'develop', 'build', 'deploy', 'verify', 'review', 'archive']
PACKAGE = Path(__file__).resolve().parent.parent


def validate_config(config):
    if not isinstance(config.get('max_attempts'), int) or not 1 <= config['max_attempts'] <= 20:
        raise FlowError('max_attempts must be between 1 and 20')
    profile = config['profile']
    for stage in ('preflight', 'baseline', 'build', 'verify'):
        if not profile.get(stage):
            raise FlowError('Configure non-empty profile.' + stage)
    if not profile.get('artifacts'):
        raise FlowError('Configure profile.artifacts; a build must produce an artifact')
    if not profile.get('environment') or 'UNCONFIGURED' in json.dumps(profile['environment']):
        raise FlowError('Configure the target hardware/kernel environment')
    if profile.get('baseline_expect') not in ('pass', 'fail'):
        raise FlowError('baseline_expect must be pass or fail')
    if profile.get('deployment_required'):
        if not profile.get('deployment_authorized'):
            raise FlowError('Deployment is not enabled for this profile')
        if not profile.get('deploy') or not profile.get('rollback'):
            raise FlowError('A deployment requires deploy and rollback commands')
    elif profile.get('deploy'):
        raise FlowError('deploy must be empty when deployment_required is false')
    for stage in ('preflight', 'baseline', 'build', 'deploy', 'verify', 'rollback'):
        for step in profile.get(stage, []):
            if not isinstance(step.get('argv'), list) or not step['argv'] or not all(
                    isinstance(arg, str) for arg in step['argv']):
                raise FlowError(stage + ' requires a non-empty argv array')
            if step.get('timeout', 600) <= 0:
                raise FlowError('Command timeout must be positive')
            if step.get('failure_code', 1) in (0, 124, 126, 127):
                raise FlowError('baseline failure_code must identify a test failure')


def create_task(root, config, request, kind, repositories):
    root = Path(root).resolve()
    if not request.strip() or not repositories:
        raise FlowError('A request and at least one repository are required')
    selected = {}
    commits = {}
    for name in repositories:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
            raise FlowError('Invalid repository name')
        if name not in config['repositories']:
            raise FlowError('Unknown repository: ' + name)
        selected[name] = copy.deepcopy(config['repositories'][name])
        commits[name] = git(selected[name]['path'], 'rev-parse', '--verify', selected[name]['ref'] + '^{commit}')
    frozen = copy.deepcopy(config)
    frozen['repositories'] = selected
    tid = 'TASK-' + uuid.uuid4().hex[:12]
    directory = root / '.accflow' / 'tasks' / tid
    directory.mkdir(parents=True)
    for name, spec in selected.items():
        if spec.get('config'):
            destination = directory / (name + '.config')
            shutil.copyfile(spec['config'], str(destination))
            spec['config'] = str(destination)
            spec['config_sha256'] = file_hash(destination)
    task = {'id': tid, 'request': request, 'kind': kind, 'created': now(),
            'config': frozen, 'config_sha256': digest(frozen), 'commits': commits,
            'stage': 'prepare', 'status': 'pending', 'attempt': 1,
            'deployment_dirty': False, 'records': [], 'failures': [], 'events': [], 'patches': {}}
    write_json(directory / 'task.json', task)
    return task


class Runner:
    def __init__(self, root, task_id):
        self.root = Path(root).resolve()
        self.directory = inside(self.root / '.accflow' / 'tasks', task_id)
        self.path = self.directory / 'task.json'
        self.task = read_json(self.path)
        self.workspace = self.directory / 'workspace'
        self.exchange = self.directory / 'exchange'
        self.library = Library(self.root / 'cases')

    def save(self, event=None):
        if event:
            self.task['events'].append({'at': now(), 'event': event,
                                        'stage': self.task['stage'], 'attempt': self.task['attempt']})
        write_json(self.path, self.task)

    def values(self):
        values = {'root': str(self.root), 'task': str(self.directory),
                  'workspace': str(self.workspace), 'attempt': str(self.task['attempt'])}
        values.update({name: str(self.workspace / name) for name in self.task['commits']})
        return values

    def expand(self, text):
        for key, value in self.values().items():
            text = text.replace('{' + key + '}', value)
        return text

    def record(self, argv, cwd, stage, timeout=600, env=None, stdin=None):
        filename = '{:04d}-{}-{}.log'.format(len(self.task['records']), stage, self.task['attempt'])
        record = execute(argv, cwd, self.directory / 'evidence' / filename, timeout, env, stdin)
        record.update({'stage': stage, 'attempt': self.task['attempt']})
        self.task['records'].append(record)
        self.save()
        return record

    def commands(self, stage):
        profile = self.task['config']['profile']
        observed_failure = False
        for step in profile.get(stage, []):
            argv = [self.expand(arg) for arg in step['argv']]
            env = dict(os.environ)
            env.update({key: self.expand(value) for key, value in step.get('env', {}).items()})
            result = self.record(argv, self.expand(step.get('cwd', '{workspace}')), stage,
                                 step.get('timeout', 600), env)
            code = result['returncode']
            if stage == 'baseline' and profile['baseline_expect'] == 'fail':
                if code == step.get('failure_code', 1):
                    observed_failure = True
                elif code != 0:
                    raise FlowError('Baseline infrastructure failed: ' + result['log'])
            elif code != 0:
                raise FlowError(stage + ' failed: ' + result['log'])
            if step.get('contains'):
                output = Path(result['log']).read_text(encoding='utf-8', errors='replace')
                if step['contains'] not in output:
                    raise FlowError(stage + ' missing required output: ' + step['contains'])
        if stage == 'baseline' and profile['baseline_expect'] == 'fail' and not observed_failure:
            raise FlowError('Baseline did not reproduce the expected failure')

    def prepare(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.exchange.mkdir(parents=True, exist_ok=True)
        for name, spec in self.task['config']['repositories'].items():
            repo = self.workspace / name
            if not repo.exists():
                result = self.record(['git', 'clone', '--no-hardlinks', '--no-checkout', '--local',
                                      spec['path'], str(repo)], self.workspace, 'clone', 1800)
                if result['returncode']:
                    raise FlowError('Clone failed: ' + result['log'])
            if not (repo / '.git').is_dir():
                raise FlowError('Incomplete repository clone: ' + str(repo))
            git(repo, 'checkout', '--detach', self.task['commits'][name])
            status = git(spec['path'], 'status', '--porcelain')
            self.task.setdefault('source_status', {})[name] = {
                'changed_entries': len(status.splitlines()),
                'has_conflicts': bool(git(spec['path'], 'diff', '--name-only', '--diff-filter=U')),
                'policy': 'Only the pinned commit is cloned; local modifications are excluded'}
            if spec.get('config'):
                if file_hash(spec['config']) != spec['config_sha256']:
                    raise FlowError('Pinned kernel config was changed')
                shutil.copyfile(spec['config'], str(repo / '.config'))
        context = self.library.search(self.task['request'] + ' ' + ' '.join(self.task['commits']),
                                      self.task['config'].get('case_context_limit', 8))
        write_json(self.directory / 'context.json', context)

    def patches(self):
        hashes = {}
        for name, commit in self.task['commits'].items():
            repo = self.workspace / name
            git(repo, 'add', '-N', '--', '.')
            patch = self.directory / 'patches' / ('{}-{}.patch'.format(name, self.task['attempt']))
            result = execute(['git', 'diff', '--binary', commit, '--'], repo, patch)
            if result['returncode']:
                raise FlowError('Could not export patch')
            hashes[name] = {'path': str(patch), 'sha256': file_hash(patch), 'bytes': patch.stat().st_size}
        return hashes

    def prompt(self, phase):
        context = read_json(self.directory / 'context.json')
        if phase == 'review':
            # Revisit old conclusions on every successful task, with bounded context.
            context += self.library.search(' '.join(self.task['commits']), 8)
        return (PACKAGE / 'prompts' / (phase + '.md')).read_text(encoding='utf-8') + '\n' + json.dumps({
            'request': self.task['request'], 'kind': self.task['kind'],
            'repositories': self.values(), 'commits': self.task['commits'],
            'environment': self.task['config']['profile']['environment'],
            'prior_cases': context, 'maintenance': self.library.load()['maintenance'],
            'failures': self.task['failures'], 'evidence': self.task['records'],
            'baseline_expect': self.task['config']['profile']['baseline_expect']}, ensure_ascii=False, indent=2)

    def agent(self, phase):
        config = self.task['config']['agent']
        report_path = self.exchange / ('{}-{}-{}.json'.format(phase, self.task['attempt'], uuid.uuid4().hex[:6]))
        schema_path = PACKAGE / 'schemas' / 'agent-result.json'
        prompt = self.prompt(phase)
        prompt_path = self.directory / ('{}-{}.prompt.txt'.format(phase, self.task['attempt']))
        prompt_path.write_text(prompt, encoding='utf-8')
        if config['kind'] == 'codex':
            argv = [config.get('executable', 'codex'), 'exec', '--json', '--skip-git-repo-check',
                    '--sandbox', 'workspace-write' if phase == 'develop' else 'read-only',
                    '--output-schema', str(schema_path), '--output-last-message', str(report_path), '-']
        elif config['kind'] == 'command':
            argv = [self.expand(arg).replace('{phase}', phase).replace('{report}', str(report_path))
                    for arg in config['argv']]
        else:
            raise FlowError('Unknown agent kind: ' + config['kind'])
        result = self.record(argv, self.workspace, phase, config.get('timeout', 1800), stdin=prompt)
        if result['returncode'] != 0 or not report_path.exists():
            raise FlowError('Agent failed or did not return structured output: ' + result['log'])
        report = read_json(report_path)
        validate_report(report)
        if digest(read_json(self.path)['config']) != self.task['config_sha256']:
            raise FlowError('Agent changed the frozen task configuration')
        if report['status'] != 'ready':
            raise FlowError('Agent blocked: ' + report['summary'])
        self.task[phase + '_report'] = report
        self.task[phase + '_report_path'] = str(report_path)

    def artifacts(self):
        collected = []
        for pattern in self.task['config']['profile']['artifacts']:
            # Globs are restricted to the disposable workspace.
            matches = sorted(self.workspace.glob(pattern))
            matches = [path for path in matches if path.is_file()]
            if not matches:
                raise FlowError('No artifact matched ' + pattern)
            for path in matches:
                inside(self.workspace, str(path.relative_to(self.workspace)))
                collected.append({'path': str(path), 'sha256': file_hash(path),
                                  'symlink': os.readlink(str(path)) if path.is_symlink() else None})
        self.task['artifacts'] = collected
        write_json(self.directory / ('artifacts-{}.json'.format(self.task['attempt'])), collected)

    def rollback(self):
        if self.task['deployment_dirty']:
            self.commands('rollback')
            self.task['deployment_dirty'] = False
            self.save('rollback completed')

    def one_stage(self, stage):
        if stage == 'prepare':
            self.prepare()
        elif stage == 'develop':
            self.agent('develop')
            self.task['patches'] = self.patches()
            if not any(patch['bytes'] for patch in self.task['patches'].values()):
                raise FlowError('Agent produced no patch')
        elif stage == 'build':
            self.commands(stage)
            self.artifacts()
        elif stage == 'deploy':
            if self.task['config']['profile']['deployment_required']:
                self.task['deployment_dirty'] = True
                self.save('deployment started; rollback required after interruption')
                self.commands(stage)
        elif stage == 'review':
            before = self.patches()
            self.agent('review')
            after = self.patches()
            if before != after:
                raise FlowError('Review modified source code; verification must be repeated')
            self.task['patches'] = after
        elif stage == 'archive':
            evidence = [record['log'] for record in self.task['records']
                        if record['stage'] == 'verify' and record['attempt'] == self.task['attempt']
                        and record['returncode'] == 0]
            if not evidence:
                raise FlowError('No successful verification evidence')
            for record in self.task['records']:
                if file_hash(record['log']) != record['sha256']:
                    raise FlowError('Evidence changed: ' + record['log'])
            report = self.task['review_report']
            for finding in report['findings']:
                if not finding['evidence'] or not set(finding['evidence']).issubset(set(evidence)):
                    raise FlowError('Each finding must cite successful verification logs')
            self.task['maintenance'] = self.library.archive(self.task, report, evidence, self.task['patches'])
        else:
            self.commands(stage)

    def run(self, dry_run=False):
        if dry_run:
            return {'id': self.task['id'], 'stages': STAGES, 'commits': self.task['commits'],
                    'profile': self.task['config']['profile'], 'executed': False}
        with lock(self.root / '.accflow' / 'runner.lock'):
            self.task = read_json(self.path)
            if self.task['status'] == 'completed':
                return self.task
            if self.task['status'] in ('running', 'recovery_required'):
                raise FlowError('Interrupted execution; use recover before resuming')
            if self.task['status'] == 'failed':
                raise FlowError('Retry budget exhausted; create a new task with the failure evidence')
            validate_config(self.task['config'])
            if digest(self.task['config']) != self.task['config_sha256']:
                raise FlowError('Frozen task config changed; create a new task')
            while self.task['stage'] in STAGES:
                stage = self.task['stage']
                self.task['status'] = 'running'
                self.save('stage started')
                print('{} attempt={} stage={}'.format(self.task['id'], self.task['attempt'], stage), flush=True)
                try:
                    self.one_stage(stage)
                except (FlowError, OSError, ValueError) as error:
                    self.task['failures'].append({'stage': stage, 'attempt': self.task['attempt'],
                                                  'error': str(error), 'at': now()})
                    try:
                        self.rollback()
                    except (FlowError, OSError) as rollback_error:
                        self.task['status'] = 'recovery_required'
                        self.save('rollback failed: ' + str(rollback_error))
                        return self.task
                    if stage in ('develop', 'build', 'verify', 'review'):
                        if self.task['attempt'] < self.task['config']['max_attempts']:
                            self.task['attempt'] += 1
                            self.task['stage'] = 'develop'
                            self.task['status'] = 'pending'
                            self.save('retry with failure evidence')
                            continue
                        self.task['status'] = 'failed'
                    else:
                        self.task['status'] = 'blocked'
                    self.save('stage failed: ' + str(error))
                    return self.task
                index = STAGES.index(stage) + 1
                self.task['stage'] = STAGES[index] if index < len(STAGES) else 'done'
                self.task['status'] = 'pending' if index < len(STAGES) else 'completed'
                if self.task['status'] == 'completed':
                    self.task['deployment_dirty'] = False
                self.save('stage completed')
            return self.task

    def recover(self):
        with lock(self.root / '.accflow' / 'runner.lock'):
            self.task = read_json(self.path)
            if self.task['status'] not in ('running', 'recovery_required'):
                raise FlowError('Task does not need recovery')
            self.rollback()
            if self.task['stage'] in ('deploy', 'verify', 'review', 'archive'):
                self.task['stage'] = 'build'
            self.task['status'] = 'pending'
            self.save('interrupted operation recovered; repeat from checkpoint')
            return self.task


def validate_report(report):
    if report.get('status') not in ('ready', 'blocked') or not isinstance(report.get('summary'), str):
        raise FlowError('Invalid agent report status/summary')
    if not isinstance(report.get('findings'), list) or not isinstance(report.get('revisions'), list):
        raise FlowError('Report needs findings and revisions arrays')
    if report['status'] == 'ready' and not report['findings']:
        raise FlowError('A ready report must contain at least one finding')
    for finding in report['findings']:
        for field in ('title', 'component', 'symptom', 'root_cause', 'resolution'):
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                raise FlowError('Finding requires ' + field)
        for field in ('lessons', 'failed_attempts', 'evidence'):
            if not isinstance(finding.get(field), list) or not all(isinstance(x, str) for x in finding[field]):
                raise FlowError('Finding requires string array ' + field)
    for proposal in report['revisions']:
        for field in ('case_id', 'action', 'reason', 'resolution'):
            if not isinstance(proposal.get(field), str):
                raise FlowError('Revision requires ' + field)
        for field in ('lessons', 'evidence'):
            if not isinstance(proposal.get(field), list) or not all(isinstance(x, str) for x in proposal[field]):
                raise FlowError('Revision requires string array ' + field)
