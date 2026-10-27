"""Read-set stability and bounded read-only execution inside the probe."""
import hashlib
import os
from pathlib import Path
import re
import stat
import sys


class Readonly:
    def __init__(self):
        self.enabled = False
        self.hashing = False
        self.reads = {}
        self.git = Path('/usr/bin/git').resolve(strict=True)
        self.directories = {}
        self.pending_open = None
        self.original_open = os.open

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        """Bind fd-relative opens to their actual directory, retaining O_NOFOLLOW.

        Python's open audit event omits dir_fd. Resolving its relative basename
        against cwd would record the wrong file or reject valid nested sources.
        """
        name = Path(os.fsdecode(path))
        if dir_fd is not None and not name.is_absolute():
            known = self.directories.get(dir_fd)
            info = os.fstat(dir_fd)
            if known is None or known[1:] != (info.st_dev, info.st_ino):
                raise PermissionError('unknown terminal input directory descriptor')
            actual = known[0] / name
        else:
            actual = name.absolute()
        prior = self.pending_open
        self.pending_open = actual
        try:
            descriptor = self.original_open(path, flags, mode, dir_fd=dir_fd)
        finally:
            self.pending_open = prior
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            self.directories[descriptor] = (actual, info.st_dev, info.st_ino)
        else:
            self.directories.pop(descriptor, None)
        return descriptor

    def fingerprint(self, path):
        self.hashing = True
        try:
            before = path.stat()
            if not path.is_file():return None
            if before.st_size > 512 * 1024 * 1024:raise ValueError('validator read exceeds bound')
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for chunk in iter(lambda:source.read(1<<20), b''):digest.update(chunk)
            after = path.stat()
            metadata = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_mode)
            if metadata(before) != metadata(after):raise ValueError('validator read moved')
            return metadata(after),digest.hexdigest()
        finally:self.hashing = False

    def audit(self, event, args):
        if not self.enabled or self.hashing:return
        if event == 'open':
            path,_mode,flags = args
            if isinstance(path,int):return
            if os.fspath(path) == os.devnull:return
            if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
                raise PermissionError('terminal validator is read-only')
            path = self.pending_open or Path(os.fsdecode(path)).absolute()
            if path.is_symlink():raise PermissionError('terminal validator symlink read refused')
            if path not in self.reads:self.reads[path] = self.fingerprint(path)
        if event in {'os.remove','os.rename','os.rmdir','os.mkdir','os.chmod','os.chown','os.link','os.symlink','os.truncate','os.utime','os.chdir'}:
            raise PermissionError('terminal validator is read-only')
        if event in {'socket.connect','socket.bind','socket.getaddrinfo'}:
            raise PermissionError('terminal validator network access refused')
        if event in {'os.system','os.posix_spawn','os.exec','os.fork','pty.spawn'}:
            raise PermissionError('terminal validator execution refused')
        if event == 'subprocess.Popen':
            executable,argv,cwd,env = args
            requested = Path(executable) if os.path.isabs(executable) else next((Path(p)/executable for p in (env or os.environ).get('PATH','').split(os.pathsep) if (Path(p)/executable).is_file()),Path('/nonexistent'))
            if requested.resolve() != self.git:
                raise PermissionError('terminal validator permits system read-only Git only')
            environment = env or os.environ
            required = {'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_OPTIONAL_LOCKS':'0','GIT_NO_REPLACE_OBJECTS':'1'}
            if any(environment.get(key)!=value for key,value in required.items()):
                raise PermissionError('uncontrolled terminal Git environment')
            permitted = set(required)|{'GIT_TERMINAL_PROMPT','GIT_PAGER','GIT_CONFIG_COUNT','GIT_CONFIG_KEY_0','GIT_CONFIG_VALUE_0','GIT_CONFIG_KEY_1','GIT_CONFIG_VALUE_1'}
            if any(key.startswith('GIT_') and key not in permitted for key in environment):
                raise PermissionError('unknown terminal Git environment')
            controls={'GIT_CONFIG_COUNT':'2','GIT_CONFIG_KEY_0':'core.hooksPath','GIT_CONFIG_VALUE_0':os.devnull,'GIT_CONFIG_KEY_1':'core.fsmonitor','GIT_CONFIG_VALUE_1':'false'}
            metadata_only=list(argv[-4:])==['rev-parse','--show-toplevel','--git-dir','--git-common-dir'] and environment.get('GIT_CONFIG_COUNT') is None
            if not metadata_only and any(environment.get(key)!=value for key,value in controls.items()):
                raise PermissionError('terminal Git execution controls missing')
            if not _git_arguments(list(argv[1:])):
                raise PermissionError('unsupported terminal read-only Git grammar')

    def start(self):
        sys.addaudithook(self.audit)
        self.enabled = True
        os.open = self.open

    def verify(self):
        for path,state in list(self.reads.items()):
            if self.fingerprint(path) != state:raise ValueError('terminal validator input changed: '+str(path))


def _git_arguments(arguments):
    index = 0
    while index < len(arguments) and arguments[index].startswith('-'):
        arg = arguments[index]
        # --literal-pathspecs only narrows path matching (bracketed file names stay literal).
        if arg in {'--no-optional-locks','--no-pager','--literal-pathspecs'}:index += 1
        elif arg=='-C' and index+1<len(arguments) and Path(arguments[index+1]).is_absolute():index += 2
        elif arg=='-c' and index+1<len(arguments) and arguments[index+1] in {
            'diff.autoRefreshIndex=false','core.fsmonitor=false','core.hooksPath=/dev/null',
            'core.excludesFile=/dev/null','core.untrackedCache=false'
        }:index += 2
        else:return False
    if index==len(arguments):return False
    command,values=arguments[index],arguments[index+1:]
    oid=lambda value:bool(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',value))
    revision=lambda value:value=='HEAD' or oid(value)
    if command=='cat-file':return values==['--batch']
    if command=='merge-base':return len(values)==2 and values[0]=='HEAD' and revision(values[1])
    if command=='rev-parse':
        if values in [['--show-toplevel'],['HEAD'],['--git-common-dir','--show-object-format'],['--show-toplevel','--git-dir','--git-common-dir']]:return True
        if len(values)==2 and values[0]=='--verify' and values[1]=='HEAD^{commit}':return True
        return (len(values)==5 and values[:3]==['--git-common-dir','--git-dir','--end-of-options']
                and values[4]=='HEAD^{commit}' and values[3].endswith('^{commit}') and oid(values[3][:-9]))
    allowed={
      'ls-files':{'-z','--cached','--others','--exclude-standard','--stage',
                  '--ignored','--directory','--exclude-per-directory=.gitignore'},
      'ls-tree':{'-r','-l','-z','-rlz','--name-only','--full-tree'},
      'diff':{'--ignore-submodules=all','--ignore-submodules=none','--name-only','-z','--binary','--no-ext-diff','--no-textconv'},
      'status':{'--porcelain','--porcelain=v1','-z','--untracked-files=all','--ignore-submodules=all','--ignore-submodules=none'},
    }
    if command not in allowed:return False
    if command=='diff' and not {'--no-ext-diff','--no-textconv'}<=set(values):return False
    if '--' in values:values=values[:values.index('--')]
    revisions=[]
    for value in values:
        if value in allowed[command]:continue
        if command=='ls-files' and value.startswith('--exclude=') and 0<len(value)<512 and not any(ord(c)<32 for c in value):continue
        if command in {'ls-tree','diff'} and revision(value):revisions.append(value)
        else:return False
    return len(revisions)==(1 if command in {'ls-tree','diff'} else 0)
