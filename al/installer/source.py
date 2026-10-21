"""Resolve immutable Git objects without checking out or executing releases."""
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit
from .engine import R

TAG = re.compile(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')


def git_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('GIT_', 'SSH_', 'DYLD_', 'LD_'))
           and k not in {'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'}}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0',
               GIT_PAGER='cat', GIT_OPTIONAL_LOCKS='0')
    return env


def git(root, *args, input=None):
    command = ['git', '--no-pager', '-c', 'core.hooksPath=' + os.devnull,
               '-c', 'core.fsmonitor=false', '-c', 'credential.helper=',
               '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always',
               '-c', 'protocol.https.allow=always', '-C', str(root), *args]
    result = subprocess.run(command, env=git_env(), input=input, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError('Git operation failed: ' + result.stderr.decode(errors='replace').strip())
    return result.stdout


def batch_blobs(root, objects):
    objects = list(dict.fromkeys(objects))
    if len(objects) > 10000:
        raise ValueError('release blob count limit exceeded')
    if not objects:
        return {}
    request = ('\n'.join(objects) + '\n').encode()
    metadata = git(root, 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)', input=request).splitlines()
    if len(metadata) != len(objects):
        raise ValueError('malformed Git batch metadata')
    sizes = []
    for oid, line in zip(objects, metadata):
        fields = line.split()
        if len(fields) != 3 or fields[0] != oid.encode() or fields[1] != b'blob' or not fields[2].isdigit():
            raise ValueError('malformed Git batch metadata')
        sizes.append(int(fields[2]))
    if sum(sizes) > 64 * 1024 * 1024:
        raise ValueError('release payload size limit exceeded')
    stream = git(root, 'cat-file', '--batch', input=request)
    offset, result = 0, {}
    for oid, size in zip(objects, sizes):
        end = stream.find(b'\n', offset)
        expected = (oid + ' blob ' + str(size)).encode()
        if end < 0 or stream[offset:end] != expected:
            raise ValueError('malformed Git batch blob header')
        start = end + 1
        if len(stream) < start + size + 1 or stream[start + size:start + size + 1] != b'\n':
            raise ValueError('truncated Git batch blob')
        result[oid] = stream[start:start + size]
        offset = start + size + 1
    if offset != len(stream):
        raise ValueError('unexpected trailing Git batch data')
    return result


def source_identity(source):
    if not isinstance(source, str) or not source or source.startswith('-') or any(c in source for c in '\n\r\0'):
        raise ValueError('invalid source; use an explicit local repository or HTTPS URL')
    if '://' in source:
        url = urlsplit(source)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('only credential-free HTTPS Git sources are supported')
        if url.path.startswith('/-'):
            raise ValueError('unsafe source URL')
        return source.rstrip('/')
    if ':' in source:
        raise ValueError('helper and SCP-style Git transports are unsupported')
    path = R.safe_absolute(source)
    if not path.is_dir():
        raise ValueError('local Git source does not exist: ' + str(path))
    bare = git(path, 'rev-parse', '--is-bare-repository').decode().strip()
    if bare == 'false':
        path = R.safe_absolute(git(path, 'rev-parse', '--show-toplevel').decode().strip())
    elif bare != 'true':
        raise ValueError('invalid Git repository source')
    return str(path)


def safe_subdir(subdir):
    if subdir in {'', '.'}:
        return ''
    path = R.relative(subdir)
    if path.as_posix() != subdir or '\\' in subdir or any(ord(c) < 32 for c in subdir):
        raise ValueError('noncanonical source subdirectory')
    return subdir


def _resolve(root, identity, subdir, version, observed_tags):
    rows = git(root, 'for-each-ref', '--format=%(refname:strip=2) %(objectname)', 'refs/tags').decode().splitlines()
    tags = {}
    for line in rows:
        name, oid = line.split(' ', 1)
        if TAG.fullmatch(name):
            tags[name] = oid
    if not tags:
        raise ValueError('source has no stable vMAJOR.MINOR.PATCH tags')
    observations = dict(observed_tags or {})
    for old_tag, old in observations.items():
        if tags.get(old_tag) != old.get('tag_object'):
            raise ValueError('previously observed tag moved or disappeared: ' + old_tag)
    tag = ('v' + version if version and not version.startswith('v') else version)
    if tag is None:
        tag = max(tags, key=lambda item: tuple(map(int, TAG.fullmatch(item).groups())))
    if not TAG.fullmatch(tag) or tag not in tags:
        raise ValueError('version must name an existing stable release tag')
    commit = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}').decode().strip()
    selected_observation = {'tag_object': tags[tag], 'commit': commit}
    if tag in observations and observations[tag] != selected_observation:
        raise ValueError('previously observed tag moved: ' + tag)
    observations[tag] = selected_observation
    prefix = subdir + '/' if subdir else ''
    rows = git(root, 'ls-tree', '-rz', '--full-tree', commit, '--', subdir or '.')
    from .manifest import load_layout, selected
    layout = load_layout()
    entries = {}
    folded = set()
    for row in rows.split(b'\0'):
        if not row:
            continue
        metadata, name = row.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        name = name.decode('utf-8', errors='strict')
        if prefix and not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        R.relative(rel)
        if '\\' in rel or any(ord(c) < 32 for c in rel) or unicodedata.normalize('NFC', rel) != rel:
            raise ValueError('unsafe Git tree path: ' + rel)
        collision = rel.casefold()
        if collision in folded:
            raise ValueError('case/normalization collision in Git tree: ' + rel)
        folded.add(collision)
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('unsafe Git mode/type: ' + rel)
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid):
            raise ValueError('invalid Git object identity')
        if len(folded) > 100000:
            raise ValueError('Git tree entry limit exceeded')
        if selected(rel, layout):
            entries[rel] = {'oid': oid, 'mode': int(mode[-3:], 8)}
    blobs = batch_blobs(root, [item['oid'] for item in entries.values()])
    files = {name: {'raw': blobs[item['oid']], 'mode': item['mode']} for name, item in entries.items()}
    return dict(source_identity=identity, subdir=subdir, tag=tag, version=tag[1:],
                tag_object=observations[tag]['tag_object'], commit=commit,
                tree=files, observations=observations)


def resolve_release(source, subdir='al', version=None, observed_tags=None):
    identity = source_identity(source)
    subdir = safe_subdir(subdir)
    if identity.startswith('https://'):
        with tempfile.TemporaryDirectory(prefix='al-source-') as directory:
            # A fresh bare mirror cannot inherit source-supplied repository config.
            git(directory, 'clone', '--bare', '--no-checkout', '--', identity, str(Path(directory) / 'repo'))
            return _resolve(Path(directory) / 'repo', identity, subdir, version, observed_tags)
    return _resolve(Path(identity), identity, subdir, version, observed_tags)
