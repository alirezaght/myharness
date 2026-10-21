#!/usr/bin/env python3
"""Concrete local prerequisites for owned, isolated complete verification."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from al.verification_plan import environment
from al.verification_process import OwnedProcess, execute
from al.verification_profile import write_json


def check(*, temporary_root=None, minimum_free_bytes=1024 * 1024 * 1024):
    root = Path(temporary_root or tempfile.gettempdir()).resolve()
    if sys.version_info < (3, 11):raise ValueError('Python 3.11 or later required')
    if int(importlib.metadata.version('pytest').split('.')[0]) < 8:raise ValueError('pytest 8 or later required')
    tools = {name: shutil.which(name) for name in ('git', 'uv')}
    if not all(tools.values()):raise ValueError('Git and uv executables required')
    if shutil.disk_usage(root).free < minimum_free_bytes:raise ValueError('insufficient disposable verification disk space')
    clock = time.get_clock_info('monotonic')
    if not clock.monotonic or not 0 < clock.resolution <= 0.01:raise ValueError('bounded monotonic clock unavailable')
    directory = Path(tempfile.mkdtemp(prefix='al-verification-preflight-', dir=root));directory.chmod(0o700)
    safe_cleanup = False
    try:
        home = directory / 'probe';home.mkdir(mode=0o700)
        env = environment(Path(__file__).resolve().parents[1], home)
        log = directory / 'output.log'
        with log.open('wb') as output:
            owner = execute([sys.executable, '-I', '-c',
                             'import os;from pathlib import Path;Path(os.environ["HOME"],"owned-marker").write_text("private")'],
                            cwd=directory, env=env, output=output, directory=home, assignment='preflight-exit', timeout=5)
        if owner.process.returncode or not owner.launch_identity or (home / 'home/owned-marker').read_text() != 'private':
            raise ValueError('owned isolated command did not complete')
        sleeper = directory / 'cancel';sleeper.mkdir(mode=0o700)
        owned = OwnedProcess(sleeper, 'preflight-cancel')
        journal = sleeper / 'journal.json'
        write_json(journal, {'status': 'RUNNING', 'owner': owned.record()})
        with log.open('ab') as output:
            try:
                owned.spawn([sys.executable, '-I', '-c', 'import time;time.sleep(60)'], cwd=directory, env=env, output=output)
                if not owned.members():raise ValueError('owned group membership unavailable')
            finally:
                cleanup = owned.cleanup()
                write_json(journal, {'status': 'FINISHED' if cleanup['complete'] else 'PENDING', 'owner': owned.record(), 'cleanup': cleanup})
        if not cleanup['complete'] or owned.process.poll() is None:
            raise ValueError('owned cancellation did not establish complete cleanup; retained=' + str(directory))
        safe_cleanup = True
    finally:
        if safe_cleanup:shutil.rmtree(directory)
    return {'schema_version': 1, 'status': 'PASS', 'checks': ['python', 'pytest', 'git', 'uv', 'private-temporary-files',
            'disk-space', 'monotonic-clock', 'isolated-command', 'kernel-start-identity', 'owned-group-cancellation'],
            'max_workers': min(8, os.cpu_count() or 1), 'cleanup_complete': True,
            'limitations': ['local tooling and owned disposable processes only; no provider authentication or external services checked']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--temporary-root');args = parser.parse_args()
    try:
        print(json.dumps(check(temporary_root=args.temporary_root), sort_keys=True));return 0
    except (OSError, ValueError, KeyError, importlib.metadata.PackageNotFoundError) as exc:
        print(json.dumps({'status': 'INFRA', 'error': str(exc)}, sort_keys=True));return 1


if __name__ == '__main__':raise SystemExit(main())
