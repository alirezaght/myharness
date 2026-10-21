"""Fresh AL argument plus kernel worktree attribution for every activity subject."""
from pathlib import Path
import shlex
import subprocess

from .activity_orphans import process_subjects
from .activity_process import process_cwd, identity as kernel_identity


def processes(inventory):
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,lstart=,command='], capture_output=True, timeout=5)
    if result.returncode or len(result.stdout) > 16 * 1024 * 1024:raise ValueError('process inspection unavailable')
    live = {}
    for line in result.stdout.decode(errors='strict').splitlines():
        parts = line.split(None, 6)
        if len(parts) != 7 or not parts[0].isdigit():raise ValueError('unrecognized process observation')
        try:argv = shlex.split(parts[6])
        except ValueError:
            if 'al_state.py' in parts[6]:raise ValueError('ambiguous AL process command')
            continue
        if not any(Path(arg).name == 'al_state.py' for arg in argv):continue
        session = None
        for index, arg in enumerate(argv):
            if arg == '--session' and index + 1 < len(argv):session = argv[index + 1]
            elif arg.startswith('--session='):session = arg.split('=', 1)[1]
        pid = int(parts[0]);cwd = process_cwd(pid)
        if cwd is None and kernel_identity(pid) is None:continue
        live.setdefault(session, []).append({'pid': pid, 'observed_start': ' '.join(parts[1:6]), 'cwd': str(cwd) if cwd else None})
    observations = []
    for kind, identity in process_subjects(inventory):
        repo = Path(identity['repository']['root'])
        if kind == 'runtime-root':
            known = {subject['session_id'] for subject_kind, subject in process_subjects(inventory)
                     if subject_kind != 'runtime-root' and subject['repository'] == identity['repository']}
            matches = [row for sid, rows in live.items() if sid is None or sid not in known for row in rows
                       if row['cwd'] is None or Path(row['cwd']) == repo or repo in Path(row['cwd']).parents]
        else:
            matches = [*live.get(identity['session_id'], []),
                       *(row for row in live.get(None, []) if row['cwd'] is None or Path(row['cwd']) == repo or repo in Path(row['cwd']).parents)]
        for row in matches:
            cwd = Path(row['cwd']) if row['cwd'] else None
            observations.append({'subject_kind': kind, 'record_identity': identity, 'pid': row['pid'], 'recorded_start': None,
                                 'observed_start': row['observed_start'],
                                 'result': 'unknown' if cwd is None else ('live-relevant' if cwd == repo or repo in cwd.parents else 'different-process'),
                                 'evidence': {'kind': 'AL arguments plus kernel cwd/worktree', 'cwd': row['cwd']}})
        if not matches:
            observations.append({'subject_kind': kind, 'record_identity': identity, 'pid': None, 'recorded_start': None, 'observed_start': None,
                                 'result': 'absent', 'evidence': 'no matching AL process; orphan locks still require held-lock and stable membership proof'})
    return observations
