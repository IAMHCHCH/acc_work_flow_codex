import argparse
import json
from pathlib import Path
import shutil
import sys

from .cases import Library
from .io import FlowError, git, lock, read_json
from .runner import Runner, create_task


def doctor(config):
    result = {'python': sys.version.split()[0], 'agent': shutil.which(config.get('agent', {}).get('executable', 'codex')),
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
    parser.add_argument('--config', default=None)
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
    start.add_argument('--headless', action='store_true', help='Use the legacy configured subprocess agent runner')
    start.add_argument('--environment', help='Environment JSON; no task-specific build commands required')
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
    sub.add_parser('next').add_argument('task_id')
    plan = sub.add_parser('plan')
    plan.add_argument('task_id'); plan.add_argument('--file', required=True)
    checkpoint = sub.add_parser('checkpoint')
    checkpoint.add_argument('task_id'); checkpoint.add_argument('--stage', required=True)
    checkpoint.add_argument('--summary', required=True)
    command = sub.add_parser('exec')
    command.add_argument('task_id'); command.add_argument('--stage', required=True)
    command.add_argument('--cwd'); command.add_argument('--check'); command.add_argument('--contains')
    command.add_argument('--expect', type=int, default=0); command.add_argument('--timeout', type=int, default=600)
    # Parse command argv after an explicit -- below, so its flags stay untouched.
    attach = sub.add_parser('attach')
    attach.add_argument('task_id'); attach.add_argument('--file', required=True)
    attach.add_argument('--kind', choices=['artifact','report','supporting'], required=True)
    for name in ['retry', 'session-recover']:
        p = sub.add_parser(name); p.add_argument('task_id'); p.add_argument('--reason', required=True)
    finish = sub.add_parser('finish')
    finish.add_argument('task_id'); finish.add_argument('--review', required=True); finish.add_argument('--report', required=True)
    install = sub.add_parser('install')
    install.add_argument('--client', choices=['codex','claude','opencode','all'], default='all')
    install.add_argument('--target', help='Project directory; omitted installs into the user skill directories')
    install.add_argument('--check', action='store_true', help='Inspect installation without writing')
    raw = sys.argv[1:]
    separator = raw.index('--') if '--' in raw else len(raw)
    command_argv = raw[separator+1:]
    args = parser.parse_args(raw[:separator])
    root = Path(args.root).resolve()
    args.config = args.config or ('config.local.json' if (root / 'config.local.json').exists() else 'config.json')
    try:
        if args.command == 'install':
            from .install import install_skills
            result = install_skills(root, args.client, args.target, args.check)
        elif args.command in ('next','plan','checkpoint','exec','attach','retry','session-recover','finish'):
            from .session import Session
            with lock(root / '.accflow' / 'native.lock'):
                session = Session(root, args.task_id)
                if args.command == 'next': result = session.next()
                elif args.command == 'plan': result = session.plan(read_json(args.file))
                elif args.command == 'checkpoint': result = session.checkpoint(args.stage, args.summary)
                elif args.command == 'exec': result = session.command(args.stage, command_argv, args.cwd, args.check, args.expect, args.contains, args.timeout)
                elif args.command == 'attach': result = session.attach(args.file, args.kind)
                elif args.command == 'retry': result = session.retry(args.reason)
                elif args.command == 'session-recover': result = session.recover(args.reason)
                else: result = session.finish(read_json(args.review), args.report)
        elif args.command == 'init':
            (root / '.accflow').mkdir(parents=True, exist_ok=True)
            result = Library(root / 'cases').consolidate()
        elif args.command in ('doctor', 'new', 'start'):
            environment = getattr(args, 'environment', None)
            config = read_json(root / (environment or args.config))
            if args.command == 'doctor':
                result = doctor(config)
            else:
                names = args.repos.split(',') if args.repos else [n for n,s in config['repositories'].items() if Path(s['path']).exists()]
                if args.command == 'start' and not args.headless:
                    from .session import start_session
                    result = start_session(root, config, args.request, args.kind, names)
                else:
                    task = create_task(root, config, args.request, args.kind, names)
                    result = task if args.command == 'new' else Runner(root, task['id']).run()
        elif args.command in ('run', 'status', 'recover'):
            runner = Runner(root, args.task_id)
            if runner.task.get('engine') == 'native' and args.command != 'status':
                raise FlowError('Native sessions are driven by the current agent: use next or session-recover')
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
        if isinstance(result, dict) and result.get('passed') is False:
            sys.exit(2)
    except (FlowError, OSError, ValueError, KeyError) as error:
        print('accflow: ' + str(error), file=sys.stderr)
        sys.exit(2)
