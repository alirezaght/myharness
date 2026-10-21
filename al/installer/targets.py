"""Conventional destinations and explicit instruction review provenance."""
import os
from pathlib import Path
from .engine import D, R
from .registry import canonical, digest
from .source import git


def resolve_target(repo=None, home=None, platforms=('codex', 'claude')):
    platforms = list(platforms)
    if not platforms or len(set(platforms)) != len(platforms) or set(platforms) - {'codex', 'claude'}:
        raise ValueError('platform must be codex, claude, or codex,claude')
    if repo:
        root = R.safe_absolute(repo)
        if not root.is_dir():
            raise ValueError('repository does not exist')
        canonical_root = Path(git(root, 'rev-parse', '--show-toplevel').decode().strip())
        root = R.safe_absolute(canonical_root)
        scope = 'repo'
        identity = D.repo_identity(root)
    else:
        root = R.safe_absolute(home or Path.home())
        scope = 'global'
        identity = {'root': str(root), 'git_dir': None, 'head': None}
    body = {'scope': scope, 'root': str(root), 'identity': R.stable(identity), 'platforms': platforms}
    # A target cannot be enrolled twice by changing its platform selection.
    body['target_id'] = digest(canonical({'scope': scope, 'root': str(root)}))
    return body


def package_path(target, platform):
    return R.safe_absolute(Path(target['root']) / ('.' + platform) / 'skills/al')


def roles_path(target, platform):
    suffix = 'agents' if platform == 'codex' else 'agents/al'
    return R.safe_absolute(Path(target['root']) / ('.' + platform) / suffix)


def config_path(target, platform):
    return R.safe_absolute(Path(target['root']) / ('.' + platform) / ('config.toml' if platform == 'codex' else 'settings.json'))


def target_descriptor(target, platform):
    path = package_path(target, platform)
    identity = D.repo_identity(path) if path.exists() else (
        dict(target['identity']) if target['scope'] == 'repo' else {'root': str(path), 'git_dir': None, 'head': None})
    return {'path': str(path), 'platform': platform, 'identity': identity,
            'instructions': D.instructions(path, identity)}


def review_for(path, identity, reviews):
    rules = D.instructions(Path(path), identity)
    if not rules:
        return {'reviewed': True, 'instructions': [], 'provenance': 'no applicable instruction files'}
    supplied = (reviews or {}).get('instructions', reviews or {})
    # A broader explicit review may cover the engine's narrower instruction set.
    for key, review in supplied.items():
        if not isinstance(review, dict) or not review.get('reviewed') or not review.get('reason'):
            continue
        covered = review.get('instructions', [])
        if all(item in covered for item in rules):
            return {'reviewed': True, 'instructions': rules, 'provenance': {'path': key, 'review': review}}
    raise ValueError('instruction review required for ' + str(path) + ': ' + ', '.join(item['path'] for item in rules))


def validate_target(target, state_root, source=None):
    root = R.safe_absolute(target['root'])
    actual = resolve_target(repo=root if target['scope'] == 'repo' else None,
                            home=root, platforms=target['platforms'])
    if actual != target:
        raise ValueError('target repository identity changed')
    state_root = R.safe_absolute(state_root)
    prohibited = [package_path(target, p) for p in target['platforms']]
    prohibited += [roles_path(target, p) for p in target['platforms']]
    if target['scope'] == 'repo':
        prohibited.append(root)
    if source and not str(source).startswith('https://'):
        prohibited.append(R.safe_absolute(source))
    for path in prohibited:
        if path == state_root or path in state_root.parents:
            raise ValueError('installer state must be outside source/target repositories')
    for platform in target['platforms']:
        for path in (package_path(target, platform), roles_path(target, platform), config_path(target, platform)):
            R.safe_absolute(path)
    if os.path.commonpath([str(state_root), str(root)]) == str(state_root):
        raise ValueError('installer state cannot contain its target')
