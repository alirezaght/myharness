"""Exact public observations and explicit synthetic overlap requests."""
import json

from test_task_attribution import run, state
from evidence_helpers import start_implementation


def inspection(cli, repo, state_file):
    st = state(state_file)
    result, out, err = cli('--session', st['session_id'], 'inspect', '--expect-run', st['run_id'],
                           '--expect-repo', str(repo), '--sections', 'attribution')
    assert result == 0, (result, out[-1000:], err[-1000:])
    report = json.loads(out)
    assert report['stable']
    return report['sections']['attribution']


def request_file(tmp_path, view, paths):
    value = {'schema_version':1, 'kind':'task-attribution-reconciliation-request-v1',
             'binding':view['reconciliation_binding'], 'paths':paths,
             'source':'Explicit synthetic supervisor observation of concurrent edit.',
             'reason':'Retain this reviewed external contribution and verify the combined candidate.'}
    path = tmp_path/'reconcile.json';path.write_text(json.dumps(value))
    return path


def overlapping(cli, repo, tmp_path):
    start_implementation(cli, tmp_path, ('svc',))
    run(cli, 'writer', 'start', '--id', 'first', '--role', 'al-engineer', '--files', 'svc')
    (repo/'svc/app.py').write_text('x = 2\n')
    run(cli, 'writer', 'finish', '--id', 'first', '--outcome', 'success')
    (repo/'svc/app.py').write_text('x = 3\n')


