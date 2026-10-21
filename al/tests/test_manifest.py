"""Focused immutable release and cache checks."""
import os
import shutil
import subprocess
from pathlib import Path
import pytest
from al.installer.manifest import build_manifest, cache_release, verify_cache
from al.installer.source import resolve_release


def git_test(root, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='AL fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='AL fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    return subprocess.run(['git', '-C', str(root), *args], env=env, check=True, capture_output=True).stdout.decode().strip()


def make_source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    canonical = Path(__file__).resolve().parents[1]
    for platform in ('codex', 'claude'):
        shutil.copytree(canonical / platform, root / 'al' / platform,
                        ignore=shutil.ignore_patterns('tests', '__pycache__', '.pytest_cache', '*.pyc'))
    # Exclusion must hold for committed tests as well as ignored checkout caches.
    tests = root / 'al/codex/skills/al/tests'
    tests.mkdir()
    (tests / 'test_never_ship.py').write_text('raise RuntimeError("must never execute")\n')
    git_test(root, 'init', '-q')
    git_test(root, 'add', '.')
    git_test(root, 'commit', '-qm', 'release one')
    git_test(root, 'tag', 'v1.0.0')
    return root


def tag_second(root, *, extra_role=False):
    skill = root / 'al/codex/skills/al/SKILL.md'
    skill.write_text(skill.read_text() + '\nRelease two.\n')
    if extra_role:
        shutil.copyfile(root / 'al/codex/agents/al-engineer.toml', root / 'al/codex/agents/al-extra.toml')
    git_test(root, 'add', '.')
    git_test(root, 'commit', '-qm', 'release two')
    git_test(root, 'tag', '-a', 'v1.1.0', '-m', 'annotated release')


def test_manifest_tag_cache_and_exclusion(tmp_path):
    source = make_source(tmp_path)
    one = resolve_release(str(source), version='1.0.0')
    manifest = build_manifest(one)
    assert manifest == build_manifest(one)
    assert all('/tests/' not in name for name in manifest['files'])
    tag_second(source)
    two = resolve_release(str(source), observed_tags=one['observations'])
    assert two['version'] == '1.1.0'
    assert two['tag_object'] != two['commit']
    cache, manifest = cache_release(two, tmp_path / 'state')
    assert verify_cache(cache, manifest['digest']) == manifest
    assert cache_release(two, tmp_path / 'state')[0] == cache
    file = cache / 'payload/codex/skills/al/SKILL.md'
    file.write_text('corrupt')
    with pytest.raises(ValueError, match='corrupt'):
        cache_release(two, tmp_path / 'state')


def test_moved_tag_refused(tmp_path):
    source = make_source(tmp_path)
    one = resolve_release(str(source))
    tag_second(source)
    git_test(source, 'tag', '-f', 'v1.0.0')
    with pytest.raises(ValueError, match='moved'):
        resolve_release(str(source), observed_tags=one['observations'])


def test_symlink_and_missing_resources_refused(tmp_path):
    source = make_source(tmp_path)
    (source / 'al/codex/skills/al/scripts/unsafe').symlink_to('/etc/passwd')
    git_test(source, 'add', '.')
    git_test(source, 'commit', '-qm', 'unsafe')
    git_test(source, 'tag', 'v1.2.0')
    with pytest.raises(ValueError, match='unsafe Git'):
        resolve_release(str(source))
    release = resolve_release(str(source), version='1.0.0')
    del release['tree']['claude/skills/al/scripts/al_readonly_guard.py']
    with pytest.raises(ValueError, match='missing required'):
        build_manifest(release)


@pytest.mark.parametrize('source,subdir', [('--upload-pack=evil', 'al'), ('ext::evil', 'al'), ('https://user@example.com/a', 'al'), ('/tmp', '../al')])
def test_unsafe_sources(source, subdir):
    with pytest.raises(ValueError):
        resolve_release(source, subdir)


def test_batched_blob_reads_and_dirty_checkout_exclusion(tmp_path, monkeypatch):
    from al.installer import source as S
    source = make_source(tmp_path)
    skill = source / 'al/codex/skills/al/SKILL.md'
    skill.write_text('dirty working tree must not ship')
    original, calls = S.git, []
    def counted(root, *args, **kwargs):
        calls.append(args)
        return original(root, *args, **kwargs)
    monkeypatch.setattr(S, 'git', counted)
    release = S.resolve_release(str(source))
    assert release['tree']['codex/skills/al/SKILL.md']['raw'] != skill.read_bytes()
    assert len([args for args in calls if args[0] == 'cat-file']) == 2
    assert all('/tests/' not in name for name in release['tree'])


@pytest.mark.parametrize('stream', [b'wrong header\n', b'{oid} blob 3\nab', b'{oid} blob 3\nabc\ntrailing'])
def test_malformed_batch_stream_refused(monkeypatch, stream):
    from al.installer import source as S
    oid = 'a' * 40
    def fake(root, *args, **kwargs):
        if args[1].startswith('--batch-check'):
            return (oid + ' blob 3\n').encode()
        return stream.replace(b'{oid}', oid.encode())
    monkeypatch.setattr(S, 'git', fake)
    with pytest.raises(ValueError, match='batch'):
        S.batch_blobs(Path('/unused'), [oid])
