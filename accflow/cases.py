import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from .io import FlowError, digest, lock, now, read_json, write_json


def normalize(value):
    return re.sub(r'\s+', ' ', str(value)).strip().casefold()


def fingerprint(case):
    return digest({'component': normalize(case['component']),
                   'root_cause': normalize(case['root_cause']),
                   'resolution': normalize(case['resolution']), 'scope': case['scope']})


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'library.json'

    def load(self):
        if not self.path.exists():
            return {'version': 1, 'cases': {}, 'events': [], 'maintenance': {}}
        return read_json(self.path)

    def search(self, query, limit=8):
        tokens = set(re.findall(r'\w+', normalize(query)))
        ranked = []
        for case in self.load()['cases'].values():
            if case['status'] != 'active':
                continue
            body = normalize(json.dumps(case, ensure_ascii=False))
            score = sum(1 for token in tokens if token in body)
            if score:
                ranked.append((score, case['id'], case))
        return [item[2] for item in sorted(ranked, key=lambda item: (-item[0], item[1]))[:limit]]

    def maintain(self, data):
        canonical = {}
        duplicates = []
        for cid, case in sorted(data['cases'].items()):
            if case['status'] != 'active':
                continue
            key = fingerprint(case)
            if key in canonical:
                target = data['cases'][canonical[key]]
                target['occurrences'] = sorted(set(target['occurrences'] + case['occurrences']))
                for field in ('lessons', 'evidence', 'failed_attempts'):
                    target[field] = list(dict.fromkeys(target[field] + case[field]))
                case['status'] = 'merged'
                case['merged_into'] = target['id']
                duplicates.append([cid, target['id']])
            else:
                canonical[key] = cid
        active = [case for case in data['cases'].values() if case['status'] == 'active']
        candidates = []
        for index, first in enumerate(active):
            for second in active[index + 1:]:
                if first['component'] != second['component']:
                    continue
                similarity = SequenceMatcher(None, normalize(first['root_cause']),
                                             normalize(second['root_cause'])).ratio()
                if similarity >= 0.75:
                    candidates.append({'cases': [first['id'], second['id']],
                                       'similarity': round(similarity, 3),
                                       'reason': 'Check scope and potentially conflicting resolutions'})
        data['maintenance'] = {'duplicates': duplicates, 'candidates': candidates,
                               'active_count': len(active)}
        return data['maintenance']

    def archive(self, task, report, evidence, patch_hashes):
        with lock(self.root / '.lock'):
            data = self.load()
            for index, finding in enumerate(report['findings']):
                cid = '{}-{:02d}'.format(task['id'], index + 1)
                if cid in data['cases']:
                    continue
                case = dict(finding)
                case.update({'id': cid, 'status': 'active', 'revision': 1,
                             'scope': task['config']['profile']['environment'],
                             'occurrences': [task['id']], 'created': now(),
                             'evidence': evidence, 'patch_hashes': patch_hashes,
                             'source_commits': task['commits']})
                data['cases'][cid] = case
                data['events'].append({'type': 'add', 'id': cid, 'at': now(),
                                       'value': json.loads(json.dumps(case))})
            for proposal in report['revisions']:
                old = data['cases'].get(proposal['case_id'])
                if old is None or old['status'] != 'active':
                    continue
                event_key = digest([task['id'], proposal])
                if any(event.get('key') == event_key for event in data['events']):
                    continue
                valid = (proposal['action'] in ('amend', 'retire') and
                         proposal['reason'].strip() and
                         old['scope'] == task['config']['profile']['environment'] and
                         bool(proposal['evidence']) and
                         set(proposal['evidence']).issubset(set(evidence)))
                if not valid:
                    data['events'].append({'type': 'proposal', 'key': event_key,
                                           'at': now(), 'value': proposal})
                    continue
                before = json.loads(json.dumps(old))
                if proposal['action'] == 'retire':
                    old['status'] = 'retired'
                else:
                    if not proposal['resolution'].strip():
                        raise FlowError('An amended case requires a resolution')
                    old['resolution'] = proposal['resolution']
                    old['lessons'] = proposal['lessons']
                old['revision'] += 1
                old['evidence'] = list(dict.fromkeys(old['evidence'] + proposal['evidence']))
                data['events'].append({'type': proposal['action'], 'key': event_key,
                                       'at': now(), 'before': before,
                                       'after': json.loads(json.dumps(old)), 'reason': proposal['reason']})
            self.maintain(data)
            write_json(self.path, data)
            return data['maintenance']

    def consolidate(self):
        with lock(self.root / '.lock'):
            data = self.load()
            result = self.maintain(data)
            write_json(self.path, data)
            return result
