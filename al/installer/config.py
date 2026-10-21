"""Semantic key ownership with conservative, bounded TOML edits."""
import copy
import json
import re
import tomllib
from .registry import pairs

ABSENT = {'present': False}


def get(document, path):
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return dict(ABSENT)
        value = value[key]
    return {'present': True, 'value': value}


def put(document, path, value):
    node = document
    for key in path[:-1]:
        if key not in node:
            node[key] = {}
        if not isinstance(node[key], dict):
            raise ValueError('configuration parent is not a table: ' + '.'.join(path))
        node = node[key]
    if value['present']:
        node[path[-1]] = copy.deepcopy(value['value'])
    else:
        node.pop(path[-1], None)


def prune(value):
    if not isinstance(value, dict):
        return value
    return {k: prune(v) for k, v in value.items() if not isinstance(v, dict) or prune(v)}


def parse(raw, format):
    text = (raw or b'').decode('utf-8')
    value = tomllib.loads(text) if format == 'toml' else json.loads(text or '{}', object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError('configuration must be an object/table')
    return value


def _toml_edit(text, path, state):
    if not all(re.fullmatch(r'[A-Za-z0-9_-]+', part) for part in path):
        raise ValueError('unsupported TOML key spelling')
    lines = text.splitlines(keepends=True)
    table, name = tuple(path[:-1]), path[-1]
    current, tables, matches = (), {(): (0, len(lines))}, []
    headers = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('['):
            match = re.fullmatch(r'\[([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\]\s*(?:#.*)?', stripped)
            current = tuple(match.group(1).split('.')) if match else None
            headers.append((current, index))
        elif current == table and re.match(r'\s*' + re.escape(name) + r'\s*=', line):
            # Managed values are single-line scalars; multiline and inline syntax refuse.
            value = line.split('=', 1)[1].strip()
            if value.startswith(('"""', "'''", '{', '[')):
                raise ValueError('unsupported managed TOML representation: ' + '.'.join(path))
            matches.append(index)
    for index, (header, start) in enumerate(headers):
        if header is not None:
            tables[header] = (start + 1, headers[index + 1][1] if index + 1 < len(headers) else len(lines))
    semantic = get(tomllib.loads(text), path)
    if semantic['present'] and len(matches) != 1:
        raise ValueError('unsupported dotted/quoted/inline TOML key; use plain tables: ' + '.'.join(path))
    if len(matches) > 1:
        raise ValueError('ambiguous TOML key: ' + '.'.join(path))
    replacement = name + ' = ' + json.dumps(state['value'], ensure_ascii=False) + '\n' if state['present'] else ''
    if matches:
        lines[matches[0]] = replacement
    elif state['present']:
        if table in tables:
            index = tables[table][1]
            if index and not lines[index - 1].endswith('\n'):
                lines[index - 1] += '\n'
            lines.insert(index, replacement)
        else:
            if lines and not lines[-1].endswith('\n'):
                lines[-1] += '\n'
            lines.extend(['\n[' + '.'.join(table) + ']\n', replacement])
    return ''.join(lines)


def same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
    return left == right


def _authorized(review, before, after):
    return (isinstance(review, dict) and review.get('reviewed') and review.get('reason')
            and same(review.get('before'), before) and same(review.get('after'), after))


def plan_config(raw, owned=None, desired=None, reviewed_changes=None, *, format='toml'):
    """Desired maps dotted paths to presence/value states; ownership retains retired keys."""
    owned = copy.deepcopy(owned or {})
    desired = desired or {}
    reviews = reviewed_changes or {}
    document = parse(raw, format)
    before = copy.deepcopy(document)
    text = (raw or b'').decode('utf-8')
    next_owned, edits = {}, {}
    for key, entry in owned.items():
        if (entry.get('active', True) or key in desired) and not same(get(document, entry['path']), entry['installed']):
            raise ValueError('owned configuration key drift: ' + key)
    for key in sorted(set(owned) | set(desired)):
        path = key.split('.')
        prior = owned.get(key)
        if prior and not prior.get('active', True) and key not in desired:
            next_owned[key] = prior
            continue
        current = get(document, path)
        wanted = desired.get(key, prior['original'] if prior else ABSENT)
        active = key in desired
        if not prior and same(current, wanted) and key == 'agents.max_depth':
            continue  # Compatible shared settings remain unowned.
        if not prior and current['present'] and not same(current, wanted) and not _authorized(reviews.get(key), current, wanted):
            raise ValueError('configuration conflict needs exact reviewed change: ' + key)
        entry = prior or {'path': path, 'original': current, 'provenance': 'installer key enrollment'}
        entry.update(installed=copy.deepcopy(wanted), active=active)
        next_owned[key] = entry
        if not same(current, wanted):
            edits[key] = wanted
            put(document, path, wanted)
            if format == 'toml':
                text = _toml_edit(text, path, wanted)
    postimage = text.encode() if format == 'toml' else (json.dumps(document, indent=2, ensure_ascii=False) + '\n').encode()
    reparsed = parse(postimage, format)
    if reparsed != document:
        raise ValueError('configuration postimage semantics mismatch')
    authorized_paths = [key.split('.') for key in edits]
    unrelated_before, unrelated_after = copy.deepcopy(before), copy.deepcopy(reparsed)
    for path in authorized_paths:
        put(unrelated_before, path, ABSENT)
        put(unrelated_after, path, ABSENT)
    if prune(unrelated_before) != prune(unrelated_after):
        raise ValueError('unrelated configuration semantics changed')
    # Retain original bytes on semantic no-op, including formatting/comments.
    if not edits:
        postimage = raw
    return {'postimage_bytes': postimage, 'next_owned_keys': next_owned,
            'unrelated_semantics': prune(unrelated_before)}


def rollback_desired(previous_owned):
    return {key: entry['installed'] for key, entry in previous_owned.items() if entry.get('active', True)}
