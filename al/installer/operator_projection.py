"""Pure authority envelopes for explicitly reviewed global package replacement."""
import ast
import copy
import json
import tomllib

from .config import parse
from .registry import canonical, digest

VERSION = 2


def frontmatter(raw):
    if not raw.startswith(b'---\n'):
        raise ValueError('missing authority frontmatter delimiter')
    end = raw.find(b'\n---\n', 4)
    if end < 0:
        raise ValueError('unterminated authority frontmatter')
    envelope = raw[:end + 5]
    envelope.decode('utf-8')
    if not envelope[4:-5].strip():
        raise ValueError('empty authority frontmatter')
    return envelope


def _contexts(configurations):
    security, display = [], []
    if not isinstance(configurations, dict):
        raise ValueError('operator configuration projection must be an object')
    for platform, configuration in configurations.items():
        if platform not in {'codex', 'claude'} or not isinstance(configuration, dict):
            raise ValueError('unsupported operator host configuration shape')
        if (not {'raw', 'desired', 'owned'} <= configuration.keys()
                or configuration['raw'] is not None and not isinstance(configuration['raw'], str)
                or not isinstance(configuration['desired'], dict) or not isinstance(configuration['owned'], dict)):
            raise ValueError('malformed operator host configuration projection')
        value = copy.deepcopy(configuration)
        raw = value['raw']
        value['raw'] = parse(raw.encode('utf-8') if raw is not None else None,
                             'toml' if platform == 'codex' else 'json')
        if platform == 'claude':
            # These are duplicate representations of the same top-level display
            # setting. Nested/other keys and every Codex field remain security.
            for representation in ('raw', 'desired', 'owned'):
                if 'statusLine' in value[representation]:
                    display.append(json.dumps(value[representation].pop('statusLine'), allow_nan=False))
        security.append(json.dumps(value, allow_nan=False))
    return security, display


def _named(name, texts):
    return any(name in text or name.split('/skills/al/', 1)[-1] in text
               or name.rsplit('/', 1)[-1] in text for text in texts)


def authority(manifest, contents, configurations):
    named, display_contexts = _contexts(configurations)
    roles, skills, protected = {}, {}, {}
    for name, item in sorted(manifest['files'].items()):
        if item['kind'] == 'role':
            raw = contents[name]
            if item['platform'] == 'codex':
                value = tomllib.loads(raw.decode('utf-8'))
                body = value.pop('developer_instructions', None)
                if not isinstance(body, str) or not body.strip():
                    raise ValueError('missing Codex role instruction body')
                # Preserve every other parsed key, including future structured permissions.
                roles[name] = {'mode': item['mode'], 'envelope': value}
                named.append(canonical(value).decode())
            else:
                envelope = frontmatter(raw)
                roles[name] = {'mode': item['mode'], 'frontmatter': envelope.decode('utf-8')}
                named.append(envelope.decode('utf-8'))
        elif name.endswith('/skills/al/SKILL.md'):
            envelope = frontmatter(contents[name])
            skills[name] = {'mode': item['mode'], 'frontmatter': envelope.decode('utf-8')}
            named.append(envelope.decode('utf-8'))
    required = {'codex/skills/al/scripts/al_models.py', 'claude/skills/al/scripts/al_models.py',
                'claude/skills/al/scripts/al_readonly_guard.py'}
    if not required <= manifest['files'].keys():
        raise ValueError('missing protected model/hook implementation')
    security_seeds = set(required)
    for name, item in sorted(manifest['files'].items()):
        if item['kind'] == 'package':
            if _named(name, named):
                security_seeds.add(name)
            if _named(name, display_contexts):
                # Freeze the direct display entry's complete resource fingerprint,
                # without treating its observational runtime imports as authority.
                protected[name] = item
    # Security has a separate visited set: an already-protected display entry may
    # also be a security seed/dependency and must then be traversed normally.
    pending, visited = list(security_seeds), set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        protected[name] = manifest['files'][name]
        if not name.endswith('.py'):
            continue
        for node in ast.walk(ast.parse(contents[name])):
            modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in modules:
                local = name.rsplit('/', 1)[0] + '/' + module.replace('.', '/') + '.py'
                if local in manifest['files'] and local not in visited:
                    pending.append(local)
    return {'version': VERSION, 'roles': roles, 'skills': skills, 'protected_resources': protected}


def projection(old, new, old_contents, new_contents, configurations):
    try:
        before, after = authority(old, old_contents, configurations), authority(new, new_contents, configurations)
        if before != after:
            raise ValueError('operator update authority envelope/model/hook resources differ')
        return {'version': VERSION, 'authority_sha256': digest(canonical(before)),
                'configuration_sha256': digest(canonical(configurations))}
    except TypeError as exc:
        raise ValueError('unsupported authority/configuration projection type') from exc
