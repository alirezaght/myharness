"""Bounded inert repository capture for advisory operator facts; no target Git execution."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from .owned_process import OwnedProcess

METADATA_BYTES = 8 << 20
INDEX_BYTES = 256 << 20
PACK_BYTES = 1 << 30
CURRENT_BYTES = 512 << 20
LOOSE_BYTES = 512 << 20
PACK_LOGICAL_BYTES = 512 << 20
PAYLOAD_BYTES = 512 << 20
MAX_FILES = 200000
MAX_OBJECTS = 200000
MAX_INDEX_ROWS = 2000000
DEADLINE = 100
GIT = Path('/usr/bin/git')


def stamp(value):
    return tuple(getattr(value, name) for name in
                 ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def relative(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 4096
            or value.startswith('/') or any(p in ('', '.', '..', '.git') for p in value.split('/'))
            or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('unsafe operator facts path')
    return value


def revision(value):
    if value is None:
        return None
    if (not isinstance(value, str) or len(value) > 256 or not value or value.startswith(('-', '/'))
            or not re.fullmatch(r'[A-Za-z0-9_./-]+', value)
            or '..' in value or any(p in ('', '.') for p in value.split('/'))):
        raise ValueError('unsupported operator facts revision')
    return value


def private_directory(path):
    """Create only ordinary owned derived directories, never following an ancestor link."""
    path = Path(path)
    for item in [*reversed(path.parents), path]:
        try:
            value = item.lstat()
        except FileNotFoundError:
            item.mkdir(mode=0o700)
            value = item.lstat()
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError('derived cache ancestor is not an ordinary directory')
    value = path.stat()
    if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
        raise ValueError('derived analysis root is not private and owned')
    return path


class Reads:
    """No-follow descriptor reads, absence observations and bounded directory membership."""
    def __init__(self, check):
        self.check, self.observations, self.directories = check, {}, {}
        self.identities = {}

    @contextmanager
    def directory(self, path):
        self.check()
        path = Path(path)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('absolute ordinary source path required')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            before = os.fstat(fd)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError('source directory changed type')
            initial = self.identities.setdefault(str(path), stamp(before)[:4])
            if initial != stamp(before)[:4]:
                raise ValueError('source directory identity changed')
            yield fd
            after = os.fstat(fd)
            named = path.lstat()
            if stamp(before) != stamp(after) or stamp(after) != stamp(named):
                raise ValueError('source directory moved during capture')
        finally:
            os.close(fd)

    def read(self, path, limit=METADATA_BYTES, *, observe=True, on_chunk=None):
        path = Path(path)
        try:
            with self.directory(path.parent) as parent:
                try:
                    fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                except FileNotFoundError:
                    row, raw = None, None
                else:
                    try:
                        before = os.fstat(fd)
                        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                            raise ValueError('unsupported or oversized source file: ' + str(path))
                        chunks, count = [], 0
                        while True:
                            self.check()
                            chunk = os.read(fd, min(1 << 20, limit + 1 - count))
                            if not chunk:
                                break
                            if on_chunk is not None:
                                on_chunk(len(chunk))
                            chunks.append(chunk)
                            count += len(chunk)
                            if count > limit:
                                raise ValueError('source byte capacity exceeded')
                        raw = b''.join(chunks)
                        after = os.fstat(fd)
                        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                        if stamp(before) != stamp(after) or stamp(after) != stamp(named) or count != before.st_size:
                            raise ValueError('source file moved during capture')
                        row = (stamp(before), hashlib.sha256(raw).hexdigest())
                    finally:
                        os.close(fd)
        except FileNotFoundError:
            ancestor = path.parent
            while True:
                try:
                    ancestor.lstat()
                    break
                except FileNotFoundError:
                    ancestor = ancestor.parent
            self.listing(ancestor)
            row, raw = None, None
        if observe:
            prior = self.observations.setdefault(str(path), (row, limit))
            if prior[0] != row:
                raise ValueError('source input changed')
        return raw

    def listing(self, path):
        path = Path(path)
        with self.directory(path) as fd:
            names = sorted(os.listdir(fd))
            if len(names) > MAX_FILES:
                raise ValueError('source directory entry capacity exceeded')
            rows = []
            for name in names:
                name.encode('utf-8', 'strict')
                value = os.stat(name, dir_fd=fd, follow_symlinks=False)
                rows.append((name, stamp(value)))
        row = tuple(rows)
        prior = self.directories.setdefault(str(path), row)
        if prior != row:
            raise ValueError('source membership changed')
        return rows

    def fence(self):
        expected_files, expected_dirs = dict(self.observations), dict(self.directories)
        for _ in range(2):
            for path in list(self.identities):
                with self.directory(path):
                    pass
            for path, (expected, limit) in expected_files.items():
                raw = self.read(path, limit)
                if (raw is None) != (expected is None):
                    raise ValueError('source presence changed')
            for path in expected_dirs:
                self.listing(path)
        for path in list(self.identities):
            with self.directory(path):
                pass


def discover(reads, repository):
    """Observe routing before allocating anything; Git never interprets target metadata."""
    with reads.directory(repository):
        pass
    pointer = repository / '.git'
    if stat.S_ISDIR(pointer.lstat().st_mode):
        git_dir = pointer
    else:
        raw = reads.read(pointer)
        if raw is None or not raw.startswith(b'gitdir: ') or raw.count(b'\n') > 1:
            raise ValueError('unsupported repository Git pointer')
        value = raw[8:].decode().strip()
        if not value or '\0' in value or '\n' in value:
            raise ValueError('invalid Git directory pointer')
        git_dir = Path(os.path.abspath(repository / value))
    with reads.directory(git_dir):
        pass
    common = reads.read(git_dir / 'commondir')
    common_dir = git_dir
    if common is not None:
        value = common.decode().strip()
        if not value or '\0' in value or '\n' in value:
            raise ValueError('invalid common directory pointer')
        common_dir = Path(os.path.abspath(git_dir / value))
        reverse = reads.read(git_dir / 'gitdir')
        if reverse is None or Path(os.path.abspath(git_dir / reverse.decode().strip())) != pointer:
            raise ValueError('linked worktree reverse binding differs')
    with reads.directory(common_dir):
        pass
    for name in ('objects', 'refs'):
        with reads.directory(common_dir / name):
            pass
    if common_dir.stat().st_uid != os.getuid() or repository.stat().st_uid != os.getuid():
        raise ValueError('foreign repository metadata owner')
    return git_dir, common_dir


class OperatorSource:
    def __init__(self, repository, commit, directory, cache_root, reads, routing, deadline):
        self.unsettled = False
        self.started, self.deadline = time.monotonic(), deadline
        self.repository, self.commit = repository, revision(commit)
        self.directory, self.cache_root = Path(directory), cache_root
        self.counts = dict(current_bytes=0, pack_index_bytes=0, decoded_index_bytes=0,
                           decoded_index_rows=0, selected_pack_bytes=0, selected_pack_logical_bytes=0, requested_payload_bytes=0,
                           demanded_objects=0, loose_object_bytes=0, git_commands=0, git_stdout_bytes=0,
                           files=0, visited_files=0)
        self.reads = reads
        self.git_dir, self.common_dir = routing
        self.work = self.directory / 'work'
        self.git = self.directory / 'git'
        self.work.mkdir(mode=0o700)
        for path in (self.git, self.git / 'objects', self.git / 'objects/pack', self.git / 'refs'):
            path.mkdir(mode=0o700)
        self.objects, self.copied, self.packs, self.pack_lookup = {}, set(), set(), {}
        self.pack_members = {}
        self.indexes_scanned, self.tree, self.members = False, None, None
        self.environment = dict(PATH=str(self.directory / 'bin'), HOME=str(self.directory / 'home'),
            TMPDIR=str(self.directory), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
            GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0',
            GIT_OPTIONAL_LOCKS='0', GIT_PAGER='cat', GIT_LITERAL_PATHSPECS='1',
            GIT_DIR=str(self.git), GIT_WORK_TREE=str(self.work),
            GIT_INDEX_FILE=str(self.git / 'index'), GIT_OBJECT_DIRECTORY=str(self.git / 'objects'),
            GIT_ATTR_NOSYSTEM='1', LC_ALL='C', AL_SERVICE='0', PYTHONDONTWRITEBYTECODE='1')
        self.environment['AL_ANALYSIS_CACHE'] = '0' if os.environ.get('AL_ANALYSIS_CACHE') == '0' else '1'
        (self.directory / 'home').mkdir(mode=0o700)
        (self.directory / 'bin').mkdir(mode=0o700)
        (self.directory / 'bin/git').symlink_to(GIT)
        self.algorithm, self.filemode = 'sha1', True
        self.inert_config = {}
        self.write_config()
        self.configs()
        self.width = 40 if self.algorithm == 'sha1' else 64
        self.write_config()
        (self.git / 'HEAD').write_text('ref: refs/heads/operator-facts\n')
        self.head = self.reads.read(self.git_dir / 'HEAD', 4096)
        self.reference_value(self.head)
        self.refs = {}
        self.capture_refs(self.common_dir / 'refs')
        self.packed_refs()
        self.info_attributes = self.reads.read(self.common_dir / 'info/attributes')
        if self.commit is None and self.info_attributes is not None:
            raise ValueError('working-copy Git info attributes unsupported')
        self.real_index = self.reads.read(self.git_dir / 'index', INDEX_BYTES)
        if self.real_index is not None:
            (self.git / 'membership-index').write_bytes(self.real_index)
        self.refuse_indirection()

    def check(self):
        if time.monotonic() > self.deadline:
            raise ValueError('operator facts capture deadline exceeded')

    def charge(self, key, count, limit):
        self.counts[key] += count
        if self.counts[key] > limit:
            raise ValueError('operator facts capacity exceeded: ' + key)
        self.check()

    def write_config(self):
        text = ('[core]\nrepositoryformatversion = ' + ('1' if self.algorithm == 'sha256' else '0') +
                '\nbare = false\nfilemode = ' + str(self.filemode).lower() +
                '\nfsmonitor = false\nhooksPath = /dev/null\nattributesFile = /dev/null\nexcludesFile = /dev/null\n' +
                '[gc]\nauto = 0\n[maintenance]\nauto = false\n[protocol]\nallow = never\n')
        if self.algorithm == 'sha256':
            text += '[extensions]\nobjectFormat = sha256\n'
        for key, value in self.inert_config.items():
            text += '[core]\n' + key + ' = ' + value + '\n'
        (self.git / 'config').write_text(text)

    def allowed(self, args, index, object_directory=None):
        if object_directory is not None:
            directory = Path(object_directory)
            if (index is not None or directory.parent != self.directory or not re.fullmatch(
                    'admission-pack-[0-9a-f]{' + str(self.width) + '}', directory.name)):
                return False
            stem = directory.name.removeprefix('admission-')
            pack = directory / 'pack'
            if (set(os.listdir(directory)) != {'pack'} or set(os.listdir(pack)) != {stem + '.idx', stem + '.pack'}):
                return False
            for path in (directory, pack, pack / (stem + '.idx'), pack / (stem + '.pack')):
                value = path.lstat()
                expected = stat.S_ISDIR if path in (directory, pack) else stat.S_ISREG
                if not expected(value.st_mode) or value.st_uid != os.getuid():
                    return False
            return (args == ['cat-file', '--batch-check']
                    or args == ['verify-pack', '--', str(pack / (stem + '.idx'))])
        if index is not None and index != self.git / 'membership-index':
            return False
        if args in (['read-tree', '--empty'], ['write-tree'], ['cat-file', '--batch'],
                    ['ls-files', '--stage', '-z'], ['update-index', '-z', '--index-info'],
                    ['hash-object', '-w', '--no-filters', '--stdin-paths'],
                    ['show-index', '--object-format=' + self.algorithm]):
            return True
        if len(args) == 3 and args[:2] == ['ls-tree', '-z']:
            return bool(re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', args[2]))
        if len(args) == 6 and args[:2] == ['config', '--file'] and args[3:] == ['--no-includes', '--null', '--list']:
            path = Path(args[2])
            return path.parent == self.directory and bool(re.fullmatch(r'config-[0-9]+', path.name))
        if args[:3] == ['check-attr', '-z', '--stdin']:
            return args[3:] == ['text', 'eol', 'crlf', 'filter', 'ident', 'working-tree-encoding']
        return (args[:3] == ['ls-files', '--others', '--exclude-per-directory=.gitignore']
                and args[-1:] == ['-z'] and all(a.startswith('--exclude=') for a in args[3:-1]))

    def command(self, args, *, data=None, limit=INDEX_BYTES, index=None, timeout=120,
                object_directory=None):
        """Only private Git argv enter here; bounded pipes retain owned cleanup on every exit."""
        self.check()
        if not self.allowed(args, index, object_directory) or data is not None and len(data) > INDEX_BYTES:
            raise ValueError('unsupported private Git command or input capacity')
        self.counts['git_commands'] += 1
        owner = OwnedProcess(self.directory / ('child-' + str(self.counts['git_commands'])), 'operator-facts-git')
        environment = dict(self.environment)
        if object_directory is not None:
            environment['GIT_OBJECT_DIRECTORY'] = str(object_directory)
        if index is not None:
            environment['GIT_INDEX_FILE'] = str(index)
        selector, output, error = selectors.DefaultSelector(), bytearray(), bytearray()
        pending = memoryview(data or b'')
        process, failure = None, None
        until = min(self.deadline, time.monotonic() + timeout)
        try:
            process = owner.spawn_streams([str(GIT), '--no-optional-locks', *args], cwd=self.work,
                env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for stream, name in ((process.stdout, 'out'), (process.stderr, 'err')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if pending:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, 'in')
            else:
                process.stdin.close()
            while selector.get_map():
                left = until - time.monotonic()
                if left <= 0:
                    raise ValueError('private Git deadline exceeded')
                for key, _ in selector.select(min(left, .1)):
                    stream = key.fileobj
                    if key.data == 'in':
                        try:
                            count = os.write(stream.fileno(), pending[:65536])
                        except BrokenPipeError:
                            pending = pending[:0]
                        else:
                            pending = pending[count:]
                        if not pending:
                            selector.unregister(stream)
                            stream.close()
                        continue
                    try:
                        raw = os.read(stream.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not raw:
                        selector.unregister(stream)
                        continue
                    if key.data == 'out':
                        self.counts['git_stdout_bytes'] += len(raw)
                    target, capacity = (output, limit) if key.data == 'out' else (error, 8192)
                    if len(target) + len(raw) > capacity:
                        raise ValueError('private Git output capacity exceeded')
                    target.extend(raw)
            process.wait(timeout=max(.001, until - time.monotonic()))
            if process.returncode:
                raise ValueError('private Git refused ' + args[0] + ': ' + error.decode(errors='replace')[-1000:])
        except BaseException as exc:
            failure = exc
        finally:
            try:
                cleanup = owner.cleanup()
                if not cleanup['complete']:
                    self.unsettled = True
                    raise OSError('operator Git cleanup unresolved')
                if cleanup['secondary_signals'] and failure is None:
                    failure = KeyboardInterrupt(cleanup['secondary_signals'][0])
            except BaseException as exc:
                self.unsettled = True
                if failure is not None:
                    failure.add_note('owned cleanup failure: ' + str(exc))
                else:
                    failure = exc
            finally:
                resources = [selector]
                if owner.process is not None:
                    resources += [owner.process.stdin, owner.process.stdout, owner.process.stderr]
                for resource in resources:
                    if resource is not None:
                        try:
                            resource.close()
                        except BaseException as exc:
                            if failure is not None:
                                failure.add_note('private stream close failed: ' + str(exc))
                            else:
                                failure = exc
        if failure is not None:
            raise failure
        return bytes(output)

    def configs(self):
        # Managed git_env disables system/global configuration; they are not inputs.
        paths = [self.common_dir / 'config']
        values = {}
        for number, path in enumerate(dict.fromkeys(paths)):
            try:
                raw = self.reads.read(path)
            except FileNotFoundError:
                # Missing conventional parent directories are absence observations as well.
                parent = path.parent
                while not parent.exists():
                    parent = parent.parent
                self.reads.listing(parent)
                continue
            if raw is None:
                continue
            captured = self.directory / ('config-' + str(number))
            captured.write_bytes(raw)
            parsed = self.command(['config', '--file', str(captured), '--no-includes', '--null', '--list'])
            for row in parsed.split(b'\0'):
                if not row:
                    continue
                key, _, value = row.partition(b'\n')
                key, value = key.decode().lower(), value.decode()
                lower = value.lower()
                if (key.startswith(('include.', 'includeif.', 'filter.'))
                        or key in {'core.hookspath', 'core.attributesfile', 'core.excludesfile', 'core.sshcommand'}
                        or key.startswith('remote.') and key.endswith('.promisor') and lower not in {'false', '0', 'no'}
                        or key == 'core.fsmonitor' and lower not in {'false', '0', 'no'}
                        or key in {'core.autocrlf', 'core.eol', 'core.safecrlf'} and lower not in {'false', '0', 'no'}):
                    raise ValueError('unsupported operator Git configuration: ' + key)
                if key.startswith('extensions.') and key != 'extensions.objectformat':
                    raise ValueError('unsupported repository extension')
                if path in {self.common_dir / 'config', self.git_dir / 'config.worktree'}:
                    values[key] = lower
        configured_worktree = values.get('core.worktree')
        if configured_worktree is not None:
            raise ValueError('explicit core.worktree routing unsupported')
        if values.get('core.bare', 'false') not in {'false', '0', 'no'}:
            raise ValueError('operator facts require a worktree')
        if values.get('extensions.objectformat', 'sha1') not in {'sha1', 'sha256'}:
            raise ValueError('unsupported object format')
        self.algorithm = values.get('extensions.objectformat', 'sha1')
        version = values.get('core.repositoryformatversion', '0')
        if version not in {'0', '1'} or self.algorithm == 'sha256' and version != '1':
            raise ValueError('unsupported repository format version')
        mode = values.get('core.filemode', 'true')
        if mode not in {'true', 'false', '1', '0', 'yes', 'no'}:
            raise ValueError('unsupported file mode configuration')
        self.filemode = mode in {'true', '1', 'yes'}
        for key in ('ignorecase', 'precomposeunicode'):
            value = values.get('core.' + key)
            if value is not None:
                if value not in {'true', 'false', '1', '0', 'yes', 'no'}:
                    raise ValueError('unsupported inert core configuration')
                self.inert_config[key] = 'true' if value in {'true', '1', 'yes'} else 'false'

    def reference_value(self, raw):
        if raw is None:
            raise ValueError('missing repository HEAD/reference metadata')
        text = raw.decode('ascii').removesuffix('\n')
        if text.startswith('ref: '):
            name = text[5:]
            revision(name)
            if (not name.startswith('refs/') or any(part.startswith('.') or part.endswith(('.', '.lock'))
                    for part in name.split('/'))):
                raise ValueError('invalid symbolic reference metadata')
        else:
            self.oid(text)
        return text

    def capture_refs(self, directory):
        try:
            rows = self.reads.listing(directory)
        except FileNotFoundError:
            self.reads.listing(directory.parent)
            return
        for name, state in rows:
            path = directory / name
            if stat.S_ISDIR(state[2]):
                self.capture_refs(path)
            elif stat.S_ISREG(state[2]):
                raw = self.reads.read(path, 4096)
                key = path.relative_to(self.common_dir).as_posix()
                revision(key)
                self.refs[key] = self.reference_value(raw)
                if len(self.refs) > MAX_FILES:
                    raise ValueError('reference capacity exceeded')
            else:
                raise ValueError('unsupported Git reference type')

    def packed_refs(self):
        raw = self.reads.read(self.common_dir / 'packed-refs')
        for row in (raw or b'').splitlines():
            if row.startswith(b'#') or not row:
                continue
            if row.startswith(b'^'):
                self.oid(row[1:].decode())
                continue
            oid, name = row.decode().split(' ', 1)
            if not re.fullmatch('[0-9a-f]{' + str(self.width) + '}', oid):
                raise ValueError('invalid packed reference')
            revision(name)
            if not name.startswith('refs/'):
                raise ValueError('invalid packed reference name')
            self.refs.setdefault(name, oid)
            if len(self.refs) > MAX_FILES:
                raise ValueError('reference capacity exceeded')

    def refuse_indirection(self):
        for name in ('objects/info/alternates', 'objects/info/http-alternates', 'info/grafts', 'shallow'):
            if self.reads.read(self.common_dir / name) is not None:
                raise ValueError('unsupported object-store indirection: ' + name)
        packdir = self.common_dir / 'objects/pack'
        try:
            rows = self.reads.listing(packdir)
        except FileNotFoundError:
            self.reads.listing(packdir.parent)
            rows = []
        for name, state in rows:
            if not stat.S_ISREG(state[2]) or name.endswith('.promisor'):
                raise ValueError('unsupported object pack member')
            if not re.fullmatch(r'pack-[0-9a-f]+\.(?:pack|idx|rev|bitmap|keep)', name):
                raise ValueError('unsupported object pack layout')

    def oid(self, value):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{' + str(self.width) + '}', value):
            raise ValueError('invalid full object identifier')
        return value

    def scan_indexes(self):
        if self.indexes_scanned:
            return
        directory = self.common_dir / 'objects/pack'
        try:
            rows = self.reads.listing(directory)
        except FileNotFoundError:
            rows = []
        for name, state in rows:
            if not name.endswith('.idx'):
                continue
            if not re.fullmatch(r'pack-[0-9a-f]{' + str(self.width) + r'}\.idx', name):
                raise ValueError('invalid pack index basename')
            raw = self.reads.read(directory / name, INDEX_BYTES - self.counts['pack_index_bytes'],
                on_chunk=lambda n: self.charge('pack_index_bytes', n, INDEX_BYTES))
            (self.directory / name).write_bytes(raw)
            decoded = self.command(['show-index', '--object-format=' + self.algorithm], data=raw,
                                   limit=INDEX_BYTES - self.counts['decoded_index_bytes'])
            self.charge('decoded_index_bytes', len(decoded), INDEX_BYTES)
            members, seen = [], set()
            for row in decoded.splitlines():
                self.charge('decoded_index_rows', 1, MAX_INDEX_ROWS)
                fields = row.split()
                if (len(row) > 256 or len(fields) not in (2, 3) or not fields[0].isdigit()
                        or len(fields) == 3 and not re.fullmatch(rb'\([0-9a-fA-F]{8}\)', fields[2])):
                    raise ValueError('malformed Git-decoded pack index')
                oid = self.oid(fields[1].decode())
                if oid in seen:
                    raise ValueError('duplicate object in Git-decoded pack index')
                seen.add(oid)
                members.append(oid)
                self.pack_lookup.setdefault(oid, name)
            if not members:
                raise ValueError('empty Git-decoded pack index')
            self.pack_members[name] = tuple(members)
        self.indexes_scanned = True

    def admit_pack(self, name, content, index):
        """Admit this pair's declared logical content, not Git's internal inflation work."""
        stem = name[:-4]
        directory = self.directory / ('admission-' + stem)
        directory.mkdir(mode=0o700)
        pack = directory / 'pack'
        pack.mkdir(mode=0o700)
        for suffix, raw in (('.pack', content), ('.idx', index)):
            with (pack / (stem + suffix)).open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
        members = self.pack_members[name]
        # No other private pack, loose duplicate or alternate can answer these OIDs.
        # Size queries themselves can inflate delta headers; time/IO bounds still apply.
        raw = self.command(['cat-file', '--batch-check'],
            data=('\n'.join(members) + '\n').encode(), object_directory=directory)
        if not raw.endswith(b'\n'):
            raise ValueError('truncated selected-pack size metadata')
        rows = raw.split(b'\n')[:-1]
        if len(rows) != len(members):
            raise ValueError('selected-pack size membership mismatch')
        total = 0
        for oid, row in zip(members, rows):
            self.check()
            fields = row.split()
            if (len(row) > 256 or len(fields) != 3 or fields[0] != oid.encode()
                    or fields[1] not in (b'blob', b'tree', b'commit', b'tag')
                    or not fields[2].isdigit()):
                raise ValueError('invalid selected-pack size metadata')
            total += int(fields[2])
        # Repeated content in another selected pack is charged again, independently.
        self.charge('selected_pack_logical_bytes', total, PACK_LOGICAL_BYTES)
        self.command(['verify-pack', '--', str(pack / name)], object_directory=directory)
        for suffix in ('.pack', '.idx'):
            os.replace(pack / (stem + suffix), self.git / 'objects/pack' / (stem + suffix))

    def materialize(self, oid):
        self.oid(oid)
        if oid in self.copied:
            return
        self.charge('demanded_objects', 1, MAX_OBJECTS)
        target = self.git / 'objects' / oid[:2] / oid[2:]
        try:
            raw = self.reads.read(self.common_dir / 'objects' / oid[:2] / oid[2:],
                                  LOOSE_BYTES - self.counts['loose_object_bytes'],
                                  on_chunk=lambda n: self.charge('loose_object_bytes', n, LOOSE_BYTES))
        except FileNotFoundError:
            self.reads.listing(self.common_dir / 'objects')
            raw = None
        if raw is not None:
            target.parent.mkdir(mode=0o700, exist_ok=True)
            target.write_bytes(raw)
        else:
            self.scan_indexes()
            name = self.pack_lookup.get(oid)
            if name is None:
                raise ValueError('demanded object unavailable')
            if name not in self.packs:
                stem = name[:-4]
                pack = self.common_dir / 'objects/pack' / (stem + '.pack')
                content = self.reads.read(pack, PACK_BYTES - self.counts['selected_pack_bytes'],
                    on_chunk=lambda n: self.charge('selected_pack_bytes', n, PACK_BYTES))
                index = self.reads.read(self.common_dir / 'objects/pack' / name, INDEX_BYTES)
                self.admit_pack(name, content, index)
                self.packs.add(name)
        self.copied.add(oid)

    def read_objects(self, oids):
        wanted = list(dict.fromkeys(self.oid(oid) for oid in oids if oid not in self.objects))
        for oid in wanted:
            self.materialize(oid)
        if wanted:
            raw = self.command(['cat-file', '--batch'], data=('\n'.join(wanted) + '\n').encode(),
                               limit=PAYLOAD_BYTES - self.counts['requested_payload_bytes'] + len(wanted) * 128)
            offset = 0
            for oid in wanted:
                end = raw.find(b'\n', offset, offset + 256)
                if end < 0:
                    raise ValueError('truncated private object header')
                fields = raw[offset:end].split()
                if (len(fields) != 3 or fields[0] != oid.encode() or fields[1] not in
                        (b'blob', b'tree', b'commit', b'tag') or not fields[2].isdigit()):
                    raise ValueError('invalid private object frame')
                length = int(fields[2])
                if length > PAYLOAD_BYTES - self.counts['requested_payload_bytes']:
                    raise ValueError('operator facts capacity exceeded: requested_payload_bytes')
                content = raw[end + 1:end + 1 + length]
                if len(content) != length or raw[end + 1 + length:end + 2 + length] != b'\n':
                    raise ValueError('truncated private object body')
                expected = hashlib.new(self.algorithm, fields[1] + b' ' + fields[2] + b'\0' + content).hexdigest()
                if expected != oid:
                    raise ValueError('private object hash mismatch')
                # Count returned payload only after its complete frame and hash verify.
                self.charge('requested_payload_bytes', len(content), PAYLOAD_BYTES)
                self.objects[oid] = (fields[1].decode(), content)
                offset = end + 2 + length
            if offset != len(raw):
                raise ValueError('trailing private object bytes')
        return {oid: self.objects[oid] for oid in oids}

    def ensure_object(self, oid, expected_type=None):
        kind, content = self.read_objects([oid])[oid]
        if expected_type is not None and kind != expected_type:
            raise ValueError('demanded object type mismatch')
        return content

    def resolve(self):
        value = self.commit
        if value == 'HEAD':
            if self.head is None:
                raise ValueError('missing committed HEAD')
            value = self.head.decode().strip()
        elif not re.fullmatch('[0-9a-f]{' + str(self.width) + '}', value):
            candidates = [value, 'refs/heads/' + value, 'refs/tags/' + value]
            matches = [self.refs[v] for v in candidates if v in self.refs]
            if len(matches) != 1:
                raise ValueError('missing or ambiguous captured revision')
            value = matches[0]
        for _ in range(16):
            if not value.startswith('ref: '):
                break
            name = value[5:]
            if name not in self.refs:
                raise ValueError('unavailable symbolic reference')
            value = self.refs[name]
        oid = self.oid(value)
        for _ in range(16):
            kind, content = self.read_objects([oid])[oid]
            if kind == 'commit':
                first = content.split(b'\n', 1)[0]
                if not first.startswith(b'tree '):
                    raise ValueError('invalid commit tree')
                return self.oid(first[5:].decode())
            if kind != 'tag':
                raise ValueError('selected revision is not a commit')
            first = content.split(b'\n', 1)[0]
            if not first.startswith(b'object '):
                raise ValueError('invalid annotated tag')
            oid = self.oid(first[7:].decode())
        raise ValueError('annotated tag depth exceeded')

    def snapshot(self):
        if self.tree is None:
            if self.commit is not None:
                self.tree = self.resolve()
            else:
                self.tree = self.working_tree()
        return self.tree

    def working_tree(self):
        import al_snapshot
        membership = self.git / 'membership-index'
        if self.real_index is None:
            self.command(['read-tree', '--empty'], index=membership)
        raw = self.command(['ls-files', '--stage', '-z'], index=membership)
        tracked = {}
        for row in raw.split(b'\0'):
            if not row:
                continue
            meta, path = row.split(b'\t', 1)
            mode, oid, stage = meta.decode().split()
            path = relative(path.decode())
            if mode not in {'100644', '100755'} or stage != '0' or path in tracked:
                raise ValueError('unsupported index stage or mode')
            self.oid(oid)
            tracked[path] = mode
        if len(tracked) > MAX_FILES:
            raise ValueError('tracked membership capacity exceeded')
        prefixes = {str(Path(path).parent) for path in tracked}
        prefixes |= {str(p) for path in tracked for p in Path(path).parents}
        frozen = {}

        def walk(directory, prefix=''):
            for name, state in self.reads.listing(directory):
                path = prefix + name
                if name == '.git':
                    if prefix:
                        raise ValueError('nested repository unsupported')
                    continue
                if stat.S_ISDIR(state[2]):
                    if name not in al_snapshot.EXCLUDED or path in prefixes:
                        (self.work / path).mkdir(mode=0o700, parents=True, exist_ok=True)
                        walk(directory / name, path + '/')
                else:
                    relative(path)
                    self.charge('visited_files', 1, MAX_FILES)
                    target = self.work / path
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    # Ignore/attribute bytes determine membership/conversion. Other bytes do
                    # not: a private placeholder lets Git select without reading ignored data.
                    data = None
                    if name in {'.gitignore', '.gitattributes'}:
                        if not stat.S_ISREG(state[2]):
                            raise ValueError('unsupported ignore/attribute source type')
                        data = self.reads.read(directory / name, METADATA_BYTES,
                            on_chunk=lambda n: self.charge('current_bytes', n, CURRENT_BYTES))
                    target.write_bytes(data or b'')
                    frozen[path] = (data, state[2])
        walk(self.repository)
        for path in tracked:
            if path not in frozen:
                if self.reads.read(self.repository / path, CURRENT_BYTES) is not None:
                    raise ValueError('index path spelling differs from captured membership')
                if Path(path).name == '.gitattributes':
                    raise ValueError('deleted index attributes require unsupported historical input')
        raw = self.command(['ls-files', '--others', '--exclude-per-directory=.gitignore',
            *[v for name in al_snapshot.EXCLUDED for v in ('--exclude=' + name + '/', '--exclude=**/' + name + '/')], '-z'],
            index=membership)
        untracked = {relative(row.decode()) for row in raw.split(b'\0') if row}
        if not untracked <= frozen.keys():
            raise ValueError('private untracked spelling differs from captured membership')
        selected = sorted(set(tracked) | untracked)
        selected = [path for path in selected if path in frozen]
        if self.convertible(selected):
            raise ValueError('working-copy conversions unsupported')
        for path in selected:
            data, mode = frozen[path]
            self.charge('files', 1, MAX_FILES)
            if not stat.S_ISREG(mode):
                raise ValueError('unsupported selected working-copy source type')
            if data is None:
                data = self.reads.read(self.repository / path, CURRENT_BYTES,
                    on_chunk=lambda n: self.charge('current_bytes', n, CURRENT_BYTES))
            if data is None:
                raise ValueError('selected source disappeared')
            (self.work / path).write_bytes(data)
            frozen[path] = (data, mode)
        self.command(['read-tree', '--empty'])
        if selected:
            def quote(path):
                return b'"' + b''.join(bytes((v,)) if 32 <= v < 127 and v not in (34, 92)
                    else ('\\%03o' % v).encode() for v in os.fsencode(self.work / path)) + b'"\n'
            raw = self.command(['hash-object', '-w', '--no-filters', '--stdin-paths'],
                               data=b''.join(quote(path) for path in selected))
            oids = raw.decode().splitlines()
            if len(oids) != len(selected):
                raise ValueError('private hash-object count mismatch')
            updates = []
            for path, oid in zip(selected, oids):
                data, mode = frozen[path]
                expected = hashlib.new(self.algorithm, b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                if self.oid(oid) != expected:
                    raise ValueError('private source blob mismatch')
                self.objects[oid] = ('blob', data)
                self.copied.add(oid)
                selected_mode = ('100755' if mode & 0o100 else '100644') if self.filemode else tracked.get(path, '100644')
                updates.append(selected_mode.encode() + b' ' + oid.encode() + b'\t' + path.encode() + b'\0')
            self.command(['update-index', '-z', '--index-info'], data=b''.join(updates))
        return self.oid(self.command(['write-tree']).decode().strip())

    def entries(self, tree):
        if self.members is not None:
            if tree != self.snapshot():
                raise ValueError('operator tree mismatch')
            return dict(self.members)
        out, pending = {}, [('', tree)]
        while pending:
            prefix, oid = pending.pop()
            if self.commit is not None:
                self.ensure_object(oid, 'tree')
            raw = self.command(['ls-tree', '-z', self.oid(oid)])
            for row in raw.split(b'\0'):
                if not row:
                    continue
                meta, name = row.split(b'\t', 1)
                mode, kind, child = meta.decode().split()
                path = relative(prefix + name.decode())
                self.oid(child)
                if path in out or len(out) + len(pending) >= MAX_FILES:
                    raise ValueError('tree membership capacity exceeded')
                if kind == 'tree' and mode == '040000':
                    pending.append((path + '/', child))
                elif kind == 'blob' and mode in {'100644', '100755'}:
                    out[path] = (mode, kind, child, None)
                else:
                    raise ValueError('unsupported tree entry')
        self.members = out
        return dict(out)

    def blob_sizes(self, oids):
        objects = self.read_objects(list(dict.fromkeys(oids)))
        if any(kind != 'blob' for kind, _ in objects.values()):
            raise ValueError('nonblob analyzer input')
        return {oid: len(content) for oid, (_, content) in objects.items()}

    def convertible(self, paths):
        if not paths:
            return set()
        import al_snapshot
        raw = self.command(['check-attr', '-z', '--stdin', *al_snapshot.CONVERSIONS],
                           data=b'\0'.join(path.encode() for path in paths) + b'\0')
        fields = raw.split(b'\0')
        if fields[-1:] != [b''] or (len(fields) - 1) % 3:
            raise ValueError('invalid private attribute response')
        return {fields[i].decode() for i in range(0, len(fields)-1, 3) if fields[i+2] != b'unspecified'}

    def run(self, args, *, env=None, data=None, timeout=120):
        if env is not None:
            raise ValueError('operator source forbids alternate Git routing')
        if args == ['cat-file', '--batch']:
            oids = (data or b'').decode().splitlines()
            rows = self.read_objects(oids)
            return b''.join(oid.encode() + b' ' + kind.encode() + b' ' + str(len(content)).encode() + b'\n' + content + b'\n'
                           for oid in oids for kind, content in [rows[oid]])
        if (len(args) == 4 and args[:3] == ['rev-parse', '--verify', '--end-of-options']
                and args[3] == str(self.commit) + '^{tree}'):
            return (self.snapshot() + '\n').encode()
        raise ValueError('unsupported operator source Git operation')

    def fence(self):
        self.reads.fence()
        self.check()


@contextmanager
def capture(repository, commit=None):
    source, path, original = None, None, None
    success, cleanup_state, failure = False, 'not-created', None
    try:
        value = str(repository)
        if not Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('operator repository must be an absolute ordinary path')
        repository = Path(value)
        root = Path.home() / '.local/state/al-distribution/analysis-index'
        if not root.is_absolute() or '..' in root.parts:
            raise ValueError('invalid private analysis location')
        deadline = time.monotonic() + DEADLINE
        def check():
            if time.monotonic() > deadline:
                raise ValueError('operator facts capture deadline exceeded')
        reads = Reads(check)
        routing = discover(reads, repository)
        if any(root.is_relative_to(directory) for directory in (repository, *routing)):
            raise ValueError('derived storage lies inside repository metadata')
        reads.fence()
        root = private_directory(root)
        path = Path(tempfile.mkdtemp(prefix='.operator-', dir=root))
        cleanup_state = 'unknown'
        original = path.lstat()
        source = OperatorSource.__new__(OperatorSource)
        source.__init__(repository, commit, path, root, reads, routing, deadline)
        yield source
        success = True
    except BaseException as exc:
        failure = exc
        raise
    finally:
        cleanup_failure = None
        if path is not None:
            try:
                current = path.lstat()
                if original is None or (current.st_dev, current.st_ino, current.st_uid) != (
                        original.st_dev, original.st_ino, original.st_uid):
                    raise ValueError('owned operator scratch identity changed; refusing cleanup')
                if source is not None and getattr(source, 'unsettled', False):
                    raise OSError('operator child settlement unknown; scratch retained')
                shutil.rmtree(path)
                cleanup_state = 'known'
            except BaseException as exc:
                cleanup_failure = exc
                if failure is not None:
                    failure.add_note('operator cleanup: ' + str(exc))
        diagnostic = {'outcome': 'success' if success and cleanup_failure is None else 'failure',
                      'cleanup': cleanup_state, 'counts': getattr(source, 'counts', None)}
        try:
            print('AL operator facts capture ' + json.dumps(diagnostic, sort_keys=True,
                  separators=(',', ':')), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # Diagnostic transport never replaces a capture/cleanup failure.
        if cleanup_failure is not None and failure is None:
            raise cleanup_failure
