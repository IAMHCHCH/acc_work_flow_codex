import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


class FlowError(Exception):
    pass


def read_json(path):
    with open(str(path), encoding='utf-8') as stream:
        return json.load(stream)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def file_hash(path):
    hasher = hashlib.sha256()
    with open(str(path), 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


@contextlib.contextmanager
def lock(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FlowError('Another workflow process holds ' + str(path))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def execute(argv, cwd, log, timeout=600, env=None, stdin=None):
    """Write output directly to disk and kill the whole process group on timeout."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = now()
    timed_out = False
    with open(str(log), 'wb') as stream:
        try:
            process = subprocess.Popen(argv, cwd=str(cwd), env=env,
                                       stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                process.communicate(input=stdin.encode('utf-8') if stdin is not None else None,
                                    timeout=timeout)
                code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                code = 124
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
        except OSError as error:
            stream.write(str(error).encode('utf-8'))
            code = 127
    return {'argv': argv, 'cwd': str(cwd), 'started': started, 'finished': now(),
            'returncode': code, 'timeout': timed_out, 'log': str(log), 'sha256': file_hash(log)}


def git(repo, *args):
    try:
        return subprocess.check_output(['git', '-C', str(repo)] + list(args),
                                       stderr=subprocess.STDOUT, timeout=120).decode('utf-8', 'replace').strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
        raise FlowError('Git failed for {}: {}'.format(repo, error))


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise FlowError('Path escapes root: ' + str(relative))
    return path
