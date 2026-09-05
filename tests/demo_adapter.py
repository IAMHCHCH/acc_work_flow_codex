"""Deterministic agent and target for exercising orchestration, not driver validation."""
import importlib.util
import json
from pathlib import Path
import py_compile
import shutil
import sys


def check(path):
    namespace = {}
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    for count in (0, 1, 63, 64, 65, 129):
        assert namespace['queue_count'](count, 64) == (count + 63) // 64, count
    print('PASS: partial queue boundary cases')


def main():
    action = sys.argv[1]
    if action == 'agent':
        phase, output, source, attempt = sys.argv[2:]
        prompt = sys.stdin.read()
        if phase == 'develop':
            expression = '(items + 1) // depth' if attempt == '1' else '(items + depth - 1) // depth'
            Path(source).write_text('def queue_count(items, depth):\n    return ' + expression + '\n')
        evidence = []
        # The JSON context is appended after the fixed prompt.
        context = json.loads(prompt[prompt.index('\n{') + 1:])
        if phase == 'review':
            evidence = [row['log'] for row in context['evidence']
                        if row['stage'] == 'verify' and row['returncode'] == 0
                        and row['attempt'] == int(attempt)]
        report = {'status': 'ready', 'summary': 'SIMULATION: queue rounding correction',
                  'findings': [{'title': 'Partial queues require ceiling division',
                                'component': 'fixture', 'symptom': 'Missing partial queue',
                                'root_cause': 'Floor division drops the partial queue',
                                'resolution': 'Use ceiling division for nonnegative item counts',
                                'lessons': ['Test 0, 1, depth-1, depth, depth+1 and multiple partial queues'],
                                'failed_attempts': ['Adding one only fails for most partial queues'],
                                'evidence': evidence}], 'revisions': []}
        Path(output).write_text(json.dumps(report))
        return
    source, target, attempt = sys.argv[2:]
    source, target = Path(source), Path(target)
    if action == 'preflight':
        assert source.is_file()
    elif action == 'baseline':
        check(source)
    elif action == 'build':
        py_compile.compile(str(source), doraise=True)
    elif action == 'deploy':
        target.mkdir(exist_ok=True)
        installed = target / 'driver.py'
        if installed.exists():
            shutil.copyfile(str(installed), str(target / ('backup-' + attempt)))
        shutil.copyfile(str(source), str(installed))
    elif action == 'verify':
        check(target / 'driver.py')
    elif action == 'rollback':
        backup = target / ('backup-' + attempt)
        if backup.exists():
            shutil.copyfile(str(backup), str(target / 'driver.py'))
        elif (target / 'driver.py').exists():
            (target / 'driver.py').unlink()
        print('rollback complete')


if __name__ == '__main__':
    main()
