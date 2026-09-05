from pathlib import Path
import sys
import tempfile

from .io import git, write_json
from .runner import Runner, create_task


def setup(root):
    root = Path(root)
    source = root / 'source'
    source.mkdir(parents=True)
    (source / 'driver.py').write_text('def queue_count(items, depth):\n    return items // depth\n')
    git(source, 'init')
    git(source, 'add', '.')
    git(source, '-c', 'user.name=Accflow Demo', '-c', 'user.email=demo@example.invalid',
        'commit', '-m', 'fixture: partial queues are undercounted')
    helper = str(Path(__file__).resolve().parent.parent / 'tests' / 'demo_adapter.py')
    def step(stage):
        return {'argv': [sys.executable, helper, stage, '{workspace}/fixture/driver.py',
                         '{task}/target', '{attempt}'], 'timeout': 30}
    config = {
        'repositories': {'fixture': {'path': str(source), 'ref': 'HEAD'}},
        'agent': {'kind': 'command', 'argv': [sys.executable, helper, 'agent', '{phase}',
                                           '{report}', '{workspace}/fixture/driver.py', '{attempt}'], 'timeout': 30},
        'max_attempts': 3, 'case_context_limit': 8,
        'profile': {'name': 'local-simulation',
                    'environment': {'hardware': 'simulation', 'kernel_release': 'none'},
                    'deployment_required': True, 'deployment_authorized': True,
                    'preflight': [step('preflight')], 'baseline': [step('baseline')],
                    'baseline_expect': 'fail', 'build': [step('build')], 'deploy': [step('deploy')],
                    'verify': [step('verify')], 'rollback': [step('rollback')],
                    'artifacts': ['fixture/driver.py']}}
    write_json(root / 'config.json', config)
    return config


def demo(root):
    parent = Path(root) / '.accflow' / 'demos'
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='demo-', dir=str(parent)))
    config = setup(directory)
    task = create_task(directory, config, 'Fix partial queue allocation in fixture', 'bugfix', ['fixture'])
    result = Runner(directory, task['id']).run()
    return {'demo': True, 'hardware_tested': False, 'directory': str(directory),
            'status': result['status'], 'attempts': result['attempt'],
            'failures': result['failures'], 'maintenance': result.get('maintenance'),
            'task_id': result['id']}
