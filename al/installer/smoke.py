"""Fixed structural checks; fetched Python and hooks are never executed."""
import ast
import tomllib
from pathlib import Path
from .engine import R
from .targets import package_path


def smoke_resources(packages, roles):
    checks = []
    for platform, package in packages.items():
        package = R.safe_absolute(package)
        if not (package / 'SKILL.md').read_text().strip():
            raise ValueError('empty installed skill')
        state = ast.parse((package / 'scripts/al_state.py').read_bytes())
        if not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'main' for node in state.body):
            raise ValueError('runtime entry point missing main')
        for path in (package / 'scripts').glob('*.py'):
            R.safe_absolute(path)
            ast.parse(path.read_bytes(), filename=str(path))
        checks.append({'platform': platform, 'check': 'runtime syntax/resources', 'status': 'PASS'})
    for name in roles:
        path = R.safe_absolute(name)
        if path.suffix == '.toml':
            role = tomllib.loads(path.read_text())
            if not role.get('model') or not role.get('developer_instructions'):
                raise ValueError('incomplete Codex role: ' + name)
        elif path.stem in {'al-architect', 'al-architect-fallback'}:
            text = path.read_text()
            if 'PreToolUse:' not in text or 'al_readonly_guard.py' not in text:
                raise ValueError('required Claude read-only hook missing: ' + name)
    return checks


def smoke_installation(target, roles):
    return smoke_resources({p: package_path(target, p) for p in target['platforms']}, roles)


def smoke_release(cache, manifest, platforms):
    payload = Path(cache) / 'payload'
    roles = [str(payload / name) for name, item in manifest['files'].items()
             if item['kind'] == 'role' and item['platform'] in platforms]
    return smoke_resources({p: payload / p / 'skills/al' for p in platforms}, roles)
