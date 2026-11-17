"""Pure authority envelopes for explicitly reviewed global package replacement."""
import ast
import tomllib

from .registry import canonical, digest

VERSION = 1


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


def authority(manifest, contents, configurations):
    roles, skills, protected, named = {}, {}, {}, [canonical(configurations).decode()]
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
    for name, item in sorted(manifest['files'].items()):
        if (name.endswith('/scripts/al_models.py') or name == 'claude/skills/al/scripts/al_readonly_guard.py'
                or item['kind'] == 'package' and any(name in text or name.split('/skills/al/', 1)[-1] in text
                       or name.rsplit('/', 1)[-1] in text for text in named)):
            protected[name] = item
    required = {'codex/skills/al/scripts/al_models.py', 'claude/skills/al/scripts/al_models.py',
                'claude/skills/al/scripts/al_readonly_guard.py'}
    if not required <= protected.keys():
        raise ValueError('missing protected model/hook implementation')
    # A hook wrapper's directly imported local implementation is security code too.
    pending = list(protected)
    while pending:
        name = pending.pop()
        if not name.endswith('.py'):
            continue
        for node in ast.walk(ast.parse(contents[name])):
            modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in modules:
                local = name.rsplit('/', 1)[0] + '/' + module.replace('.', '/') + '.py'
                if local in manifest['files'] and local not in protected:
                    protected[local] = manifest['files'][local]
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
