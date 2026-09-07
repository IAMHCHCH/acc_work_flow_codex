"""Install the same host-native skill into supported client discovery paths."""
import json
from pathlib import Path
import shutil
from .io import FlowError, file_hash, write_json


def install_skills(root, client='all', target=None, check=False, home=None):
    root = Path(root).resolve()
    home = Path(home) if home else Path.home()
    source = root / 'plugins/acc-work-flow-codex/skills/accflow'
    bases = {'codex': home / '.agents/skills', 'claude': home / '.claude/skills',
             'opencode': home / '.config/opencode/skills'}
    if target:
        target = Path(target).resolve()
        bases = {c: target / p for c,p in [('codex','.agents/skills'),('claude','.claude/skills'),('opencode','.opencode/skills')]}
    results = []
    for name in (list(bases) if client == 'all' else [client]):
        destination = bases[name] / 'accflow'
        marker = destination / 'accflow-location.json'
        expected = {str(p.relative_to(source)): file_hash(p) for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
        if check:
            matches = marker.exists() and json.loads(marker.read_text()).get('root') == str(root)
            matches = matches and all((destination / p).is_file() and file_hash(destination / p) == h for p,h in expected.items())
            results.append({'client': name, 'path': str(destination), 'current': bool(matches)})
            continue
        if destination.exists() and (not marker.exists() or json.loads(marker.read_text()).get('root') != str(root)):
            raise FlowError('Another skill owns ' + str(destination) + '; choose another project target')
        for relative in expected:
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, output)
        write_json(marker, {'root': str(root), 'client': name, 'files': expected})
        results.append({'client': name, 'path': str(destination), 'installed': True})
    return results
