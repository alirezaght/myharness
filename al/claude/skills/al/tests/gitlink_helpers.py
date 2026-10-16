"""Local parent/child repositories for opaque Git-entry lifecycle tests."""
import shutil
import pytest
import al_scope as S
from evidence_helpers import git


def oid(root):
    return S.git_bytes('rev-parse', 'HEAD', root=root).decode().strip()


def commit_child(path, text):
    (path / 'file').write_text(text)
    git(path, 'add', '.')
    git(path, 'commit', '-qm', text)
    return oid(path)


@pytest.fixture
def gitlink(repo, tmp_path):
    child = tmp_path / 'child'
    child.mkdir()
    git(child, 'init', '-q')
    commit_child(child, 'A')
    git(repo, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(child), 'svc/vendor')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'parent A')
    return repo / 'svc/vendor', oid(child), oid(repo)


def remove_child(path, empty=False):
    shutil.rmtree(path)
    if empty:
        path.mkdir()


def row_at(rows, path='svc/vendor'):
    return next(row for row in rows if row['path'] == path)


def state(base):
    return {'baseline_head': base, 'identity': {'merge_base': base, 'target_oid': base}}


def source_file(tmp, capability='parent-entry', path='svc/vendor'):
    import json
    file=tmp/'sources.json'
    file.write_text(json.dumps([{'path':path,'capability':capability}]))
    return str(file)


def gate_plan(cli, tmp, argv, scope, sources=(), cwd='.'):
    import json
    import al_artifacts as A
    from batch13_helpers import manifest
    manifest(cli,tmp,argv)
    st=json.loads(cli('show')[1]);data=A.read_json(st['gate_manifests'][-1])
    gate=next(g for g in data['gates'] if g['id']=='boundary')
    gate.update(scope=scope,sources=list(sources),cwd=cwd)
    file=tmp/'declared-gate.json';file.write_text(json.dumps(data))
    result=cli('gates','plan','--file',str(file))
    assert result[0]==0,result
