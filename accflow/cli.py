import argparse
import json
from pathlib import Path
import shutil
import sys

from .cases import Library
from .io import FlowError, git, read_json
from .runner import Runner, create_task


def doctor(config):
    result = {'python': sys.version.split()[0], 'agent': shutil.which(config['agent'].get('executable', 'codex')),
              'repositories': {}, 'profile': config['profile']['name']}
    for name, spec in config['repositories'].items():
        try:
            status = git(spec['path'], 'status', '--porcelain')
            entry = {'commit': git(spec['path'], 'rev-parse', spec['ref'] + '^{commit}'),
                     'changed_entries': len(status.splitlines()),
                     'conflicts': len(git(spec['path'], 'diff', '--name-only', '--diff-filter=U').splitlines())}
            if spec.get('config'):
                wanted = ('CONFIG_UACCE=', 'CONFIG_PCI_PASID=', 'CONFIG_ARM_SMMU',
                          'CONFIG_IOMMU_SVA=', 'CONFIG_CRYPTO_DEV_HISI', 'CONFIG_HISI_ACC_VFIO')
                entry['kernel_config'] = [line for line in Path(spec['config']).read_text().splitlines()
                                          if line.startswith(wanted)]
            result['repositories'][name] = entry
        except (FlowError, OSError) as error:
            result['repositories'][name] = {'error': str(error)}
    return result


def main():
    parser = argparse.ArgumentParser(description='HiSilicon evidence-based development workflow')
    parser.add_argument('--root', default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument('--config', default=('config.local.json' if (Path(__file__).resolve().parent.parent / 'config.local.json').exists() else 'config.json'))
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('doctor')
    sub.add_parser('init')
    new = sub.add_parser('new')
    new.add_argument('--request', required=True)
    new.add_argument('--kind', choices=['bugfix', 'feature'], default='bugfix')
    new.add_argument('--repos', default='uadk', help='Comma-separated repository names')
    start = sub.add_parser('start', help='Create and run a task from one request')
    start.add_argument('--request', required=True)
    start.add_argument('--kind', choices=['bugfix', 'feature', 'analysis'], default='analysis')
    start.add_argument('--repos', default=None, help='Optional comma-separated repositories; inferred when omitted')
    run = sub.add_parser('run')
    run.add_argument('task_id')
    run.add_argument('--dry-run', action='store_true')
    for name in ('status', 'recover'):
        sub.add_parser(name).add_argument('task_id')
    sub.add_parser('consolidate')
    search = sub.add_parser('search')
    search.add_argument('query')
    search.add_argument('--limit', type=int, default=8)
    sub.add_parser('demo')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        if args.command == 'init':
            (root / '.accflow').mkdir(parents=True, exist_ok=True)
            result = Library(root / 'cases').consolidate()
        elif args.command in ('doctor', 'new', 'start'):
            config = read_json(root / args.config)
            if args.command == 'doctor':
                result = doctor(config)
            else:
                names = args.repos.split(',') if args.repos else list(config['repositories'])
                task = create_task(root, config, args.request, args.kind, names)
                result = task if args.command == 'new' else Runner(root, task['id']).run()
        elif args.command in ('run', 'status', 'recover'):
            runner = Runner(root, args.task_id)
            result = runner.task if args.command == 'status' else (
                runner.run(args.dry_run) if args.command == 'run' else runner.recover())
        elif args.command == 'consolidate':
            result = Library(root / 'cases').consolidate()
        elif args.command == 'search':
            result = Library(root / 'cases').search(args.query, args.limit)
        elif args.command == 'demo':
            from .demo import demo
            result = demo(root)
        else:
            parser.print_help()
            return
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get('status') in ('blocked', 'failed', 'recovery_required'):
            sys.exit(2)
    except (FlowError, OSError, ValueError, KeyError) as error:
        print('accflow: ' + str(error), file=sys.stderr)
        sys.exit(2)
