"""Repository-specific review guidance travels with the immutable review packet."""
import hashlib
import al_review_rules as R


def test_capture_follows_hook_rule_files_with_provenance_without_executing(monkeypatch,tmp_path):
    root=tmp_path/'rulesrepo';root.mkdir(exist_ok=True);(root/'.githooks').mkdir();(root/'docs').mkdir()
    hook=root/'.githooks/pre-commit'
    hook.write_text('#!/bin/sh\ncat docs/review-policy.md\ntouch MUST-NOT-EXECUTE\n')
    policy=root/'docs/review-policy.md';policy.write_text('LOCAL-42: check request retries. See details.md\n')
    (root/'docs/details.md').write_text('Apply the repository-specific contract.\n')
    (root/'AGENTS.md').write_text('Follow the committed hook.\n')
    monkeypatch.setattr(R.al_store,'REPO_ROOT',root)
    snapshots=[]
    def snapshot(sid,raw,kind,source):
        snapshots.append((raw,source));return {'sha256':hashlib.sha256(raw).hexdigest()}
    monkeypatch.setattr(R.A,'snapshot_bytes',snapshot)
    result=R.capture({},'session')
    rows={r['path']:r for r in result['sources']}
    assert set(rows)=={'AGENTS.md','.githooks/pre-commit','docs/review-policy.md','docs/details.md'}
    assert rows['docs/review-policy.md']['content']==policy.read_text()
    assert rows['docs/review-policy.md']['sha256']==hashlib.sha256(policy.read_bytes()).hexdigest()
    assert not (root/'MUST-NOT-EXECUTE').exists()
    assert not result['omissions']


def test_custom_manifest_hook_sources_and_limits_are_explicit(monkeypatch,tmp_path):
    (tmp_path/'custom').mkdir();(tmp_path/'custom/check.sh').write_text('cat custom/large.md\n')
    (tmp_path/'custom/large.md').write_text('x'*131073)
    monkeypatch.setattr(R.al_store,'REPO_ROOT',tmp_path)
    monkeypatch.setattr(R.A,'read_json',lambda ref:{'rules':[{'path':'custom/check.sh'}]})
    monkeypatch.setattr(R.A,'snapshot_bytes',lambda *args:{'sha256':'a'*64})
    result=R.capture({'gate_manifests':[{}]},'session')
    assert [r['path'] for r in result['sources']]==['custom/check.sh']
    assert result['omissions']==[{'path':'custom/large.md','reason':'review instruction capture limit'}]
