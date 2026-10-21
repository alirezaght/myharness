"""Batched indexed reads preserve identity and reject damaged object streams."""
import hashlib
import io
import json
import subprocess
import pytest
import al_scope as S
import al_ledger as L


def test_records_use_one_batch_with_duplicate_empty_binary_and_symlink(repo,monkeypatch):
    for name,data in [('empty',b''),('binary',b'\0\xff\n'),('same',b'\0\xff\n')]:
        (repo/name).write_bytes(data)
    (repo/'link').symlink_to('binary')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    real=S.subprocess.Popen;calls=[]
    def spawn(argv,*args,**kwargs):
        if 'cat-file' in argv:calls.append(argv)
        return real(argv,*args,**kwargs)
    monkeypatch.setattr(S.subprocess,'Popen',spawn)
    rows=S.records('HEAD')
    assert len(calls)==1 and calls[0][-1]=='--batch'
    for row in rows:
        entry=row['index']
        if entry:
            raw=subprocess.run(['git','show',':'+row['path']],cwd=repo,check=True,capture_output=True).stdout
            assert entry['sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('response,exitcode',[(b'x missing\n',0),(b'x tree 0\n\n',0),(b'x blob 9\nabc',0),(b'x blob 0\n!',0),(b'x blob 0\n\n',1),(b'y blob 0\n\n',0)])
def test_batch_rejects_bad_frames_and_reaps(repo,monkeypatch,response,exitcode):
    class Process:
        stdin=io.BytesIO();stdout=io.BytesIO(response);killed=False;waited=False
        def poll(self):return None if not self.waited else exitcode
        def wait(self):self.waited=True;return exitcode
        def kill(self):self.killed=True
    proc=Process();original=S.subprocess.Popen
    def spawn(argv,*args,**kwargs):
        return proc if 'cat-file' in argv and '--batch' in argv else original(argv,*args,**kwargs)
    monkeypatch.setattr(S.subprocess,'Popen',spawn)
    with pytest.raises(SystemExit):S._blob_hashes(repo,['x'])
    assert proc.waited and proc.stdout.closed and proc.stdin.closed


def test_empty_index_does_not_start_batch_process(repo,monkeypatch):
    subprocess.run(['git','rm','--cached','-r','.'],cwd=repo,check=True,capture_output=True)
    original=S.subprocess.Popen;calls=[]
    def spawn(argv,*args,**kwargs):
        if 'cat-file' in argv:calls.append(argv)
        return original(argv,*args,**kwargs)
    monkeypatch.setattr(S.subprocess,'Popen',spawn)
    rows=S.records('HEAD')
    assert calls==[] and all(row['index'] is None for row in rows)


def test_batch_pipe_failure_is_refused_and_cleanup_cannot_override_it(repo,monkeypatch):
    class BrokenInput(io.BytesIO):
        def write(self,data):raise BrokenPipeError('batch exited')
        def close(self):
            super().close();raise BrokenPipeError('close failed')
    class Process:
        stdin=BrokenInput();stdout=io.BytesIO();waited=False;killed=False
        def poll(self):return None if not self.waited else 1
        def kill(self):self.killed=True
        def wait(self):self.waited=True;return 1
    proc=Process();original=S.subprocess.Popen
    def spawn(argv,*args,**kwargs):
        return proc if 'cat-file' in argv and '--batch' in argv else original(argv,*args,**kwargs)
    monkeypatch.setattr(S.subprocess,'Popen',spawn)
    with pytest.raises(SystemExit,match='batch failed'):S._blob_hashes(repo,['x'])
    assert proc.killed and proc.waited and proc.stdin.closed and proc.stdout.closed


def test_complete_candidate_keeps_vendor_files_outside_narrow_gate_scope(cli,repo):
    vendor=repo/'vendor/Assets/Feel/asset.dat'
    vendor.parent.mkdir(parents=True)
    vendor.write_bytes(b'asset version one\n')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    subprocess.run(['git','-c','user.name=t','-c','user.email=t@t','commit','-qm','seed vendor asset'],cwd=repo,check=True)
    cli('start','--spec','Keep full candidate identity independent from narrow verification scopes.')
    state=json.loads(cli('show')[1])
    candidate_before=S.capture_candidate(state)['fingerprint']
    service_scope_before=L.scope_fingerprint(['svc'])
    vendor.write_bytes(b'asset version two\n')
    candidate_after=S.capture_candidate(state)['fingerprint']
    service_scope_after=L.scope_fingerprint(['svc'])
    assert candidate_after!=candidate_before
    assert service_scope_after==service_scope_before
