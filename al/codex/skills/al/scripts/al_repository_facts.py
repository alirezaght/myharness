"""Bounded read-only snapshots and semantic coverage provenance."""
import hashlib
from collections import OrderedDict
from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import selectors
import stat
import time
import tomllib
from al_repository_capture import CaptureMixin, CaptureDrift, CaptureUnavailable, capture_discovery, immutable_observation, opaque_owner, literal_opaque_exclusions
from al_profile import measured
import al_writer_progress as progress

SOURCE_SUFFIXES=('.py','.js','.jsx','.mjs','.cjs','.ts','.tsx','.go','.rs','.java','.kt','.c','.cc','.cpp','.rb','.php','.swift','.cs')
# `bytes` bounds retained Snapshot payload, not cumulative source volume. One
# capture pass is bounded by files * file_bytes for each of current/base; parser
# work remains separately bounded per source. No cross-command stat cache is used.
LIMITS={'files':50000,'bytes':64*1024*1024,'file_bytes':2*1024*1024,'depth':32}
EXCLUDED={'.git','.codex','.claude','.venv','node_modules','__pycache__','.pytest_cache','.ruff_cache','dist','build','.next','.turbo'}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def safe(path):
    p=Path(path)
    if not isinstance(path,str) or not path or p.is_absolute() or '..' in p.parts or p.as_posix()!=path:raise ValueError('unsafe coverage path: '+str(path))
    return path


def unit_depth(path):return len(Path(path).parts)


def within(parent,path):return parent=='.' or parent==path or path.startswith(parent.rstrip('/')+'/')


def all_within(scopes,paths):
    """Whether every path lies within some scope: each path's ancestors against a set, not path x scope."""
    parents={s.rstrip('/') for s in scopes}
    if '.' in parents:return True
    for path in paths:
        parts=path.split('/')
        if not any('/'.join(parts[:i]) in parents for i in range(1,len(parts)+1)):return False
    return True


def gap(code,path,reason,unit=None,category=None):
    return dict(code=code,path=path,reason=reason,unit=unit,**({'category':category} if category else {}))


MANIFESTS={'pyproject.toml','package.json','pnpm-workspace.yaml','go.mod','go.sum','go.work','go.work.sum','setup.cfg','setup.py','Cargo.toml','.gitignore','repository.json'}


def file_identity(value):
    return (value.st_dev,value.st_ino,value.st_mode,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def priority(path):
    parts=Path(path).parts;name=Path(path).name
    supported=len(parts)==1 or len(parts)==3 and parts[0] in {'apps','services','packages'} or path=='.al/repository.json'
    return (0 if name=='.gitignore' or name in MANIFESTS and supported else 1 if name in MANIFESTS else 2,path)


def git_records(root,args,limits):
    """Consume a bounded NUL stream and always reap our child, including on overflow."""
    cap=limits['files'];byte_cap=max(4096,min(64*1024*1024,cap*4096))
    rows=[];pending=b'';total=0;deadline=time.monotonic()+10
    proc=subprocess.Popen(['git','--no-optional-locks','-c','core.excludesFile=/dev/null','-c','core.fsmonitor=false','-c','core.untrackedCache=false',*args],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    selector=selectors.DefaultSelector()
    try:
        selector.register(proc.stdout,selectors.EVENT_READ)
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('Git membership timeout')
            if not selector.select(remaining):raise ValueError('Git membership timeout')
            chunk=os.read(proc.stdout.fileno(),min(65536,byte_cap-total+1))
            if not chunk:break
            total+=len(chunk)
            if total>byte_cap:raise ValueError('Git membership output byte limit')
            pending+=chunk
            while b'\0' in pending:
                row,pending=pending.split(b'\0',1)
                if len(rows)>=cap:raise ValueError('Git membership file-limit')
                rows.append(row)
        if pending or proc.wait(timeout=max(.01,deadline-time.monotonic())):raise ValueError('Git membership failed/truncated')
        return rows
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Git membership timeout') from exc
    finally:
        selector.close();proc.stdout.close()
        if proc.poll() is None:proc.kill()
        proc.wait()


class CapturedContent(Mapping):
    """Exact captured versions; ordinary payloads are streamed, not retained en masse.

    Only the Snapshot's bounded, process-local byte cache retains payloads. Current
    reads must still match the initial no-follow identity and hash; immutable base
    bytes are verified against their Git OID and captured SHA256. Keys/metadata are
    complete even when aggregate source size exceeds the retained-byte allowance.
    """
    def __init__(self,scan,snapshot):
        self.scan=scan;self.snapshot=snapshot;self.records={}

    def __iter__(self):return iter(self.records)
    def __len__(self):return len(self.records)
    def __contains__(self,path):return path in self.records

    def __getitem__(self,path):
        record=self.records[path]
        if self.snapshot=='current':
            self.scan.safe_file(path)
            if file_identity((self.scan.root/path).lstat())!=record['identity']:
                raise CaptureDrift('source-content-or-identity', [path])
        cached=self.scan.cached((self.snapshot,path))
        if cached is not None:
            self.verify(path,cached);return cached
        if self.snapshot=='current':
            data,identity=self.scan.read_observed(path)
            if identity!=record['identity']:raise CaptureDrift('source-content-or-identity', [path])
        else:
            from al_git_cache import blobs
            data=blobs(self.scan.root,[record['oid']],max_bytes=LIMITS['file_bytes'],file_bytes=LIMITS['file_bytes'])[record['oid']]
        self.verify(path,data)
        self.scan.retain((self.snapshot,path),data)
        return data

    def verify(self,path,data):
        if hashlib.sha256(data).hexdigest()!=self.records[path]['sha256']:
            raise CaptureDrift('source-content', [path])

    def items(self):return self.content_items(list(self))

    def content_items(self,paths):
        if self.snapshot=='current':
            for path in paths:yield path,self[path]
        else:
            from al_git_cache import blobs
            for batch in self.scan.base_batches(paths):
                values=blobs(self.scan.root,[self.records[p]['oid'] for p in batch],max_bytes=self.scan.batch_bytes,file_bytes=LIMITS['file_bytes'])
                for path in batch:
                    data=values[self.records[path]['oid']];self.verify(path,data)
                    self.scan.retain((self.snapshot,path),data)
                    yield path,data

    def hashes(self):return {p:r['sha256'] for p,r in self.records.items()}


class Snapshot(CaptureMixin):
    def __init__(self,root,base_oid,paths,opaque,required_adapter=None):
        if any(type(v) is not int or v<=0 for v in LIMITS.values()):raise ValueError('invalid coverage limits')
        self.required_adapter=required_adapter;self.root=Path(root).resolve();self.base_oid=base_oid
        self.paths=[];path_overflow=False
        for path in paths:
            if len(self.paths)>=LIMITS['files']:path_overflow=True;break
            self.paths.append(safe(path))
        self.paths=sorted(set(self.paths));self.opaque=sorted(set(safe(p) for p in opaque))
        self.gaps=[];self.files=CapturedContent(self,'current');self.base=CapturedContent(self,'base');self.modes={};self.base_modes={};self.rules=set();self.absent=set();self.total=0
        self.initialize_capture()
        self.cache=OrderedDict();self.retained_bytes=0;self.peak_retained_bytes=0
        self.batch_bytes=max(LIMITS['file_bytes'],min(LIMITS['bytes'],4*1024*1024))
        if path_overflow:self.gaps.append(gap('file-limit','.', 'explicit path membership limit'))
        if any(type(v) is not int or v<=0 for v in LIMITS.values()):raise ValueError('invalid coverage limits')
        self.listing_observation=self.observe_listing()
        self.install_listing(self.listing_observation)
        for path in self.paths:self.observe_path(path)
        progress.report('inventory-current', count=0)
        for count,p in enumerate(sorted(self.members,key=lambda p:(p not in self.priority_inputs,priority(p))),1):
            affected=any(within(path,p) for path in self.paths)
            if not p.endswith('/') and affected:self.observe_path(p)
            if not p.endswith('/') and (self.relevant(p) or affected and p.endswith(('.md','.markdown','.mdx'))) and (self.root/p).exists():
                try:
                    data,identity=self.read_observed(p)
                    self.total+=len(data)
                    self.files.records[p]={'sha256':hashlib.sha256(data).hexdigest(),'identity':identity,'size':len(data)}
                    self.retain(('current',p),data);self.modes[p]=identity[2]&0o7777
                    if Path(p).name=='.gitignore':self.rules.add(p)
                except CaptureDrift:raise
                except (OSError,ValueError) as exc:self.gaps.append(gap('unreadable-input',p,str(exc)))
            progress.report('inventory-current', count=count)
        progress.report('inventory-current', count=len(self.members), force=True)
        self.read_base()

    @staticmethod
    def relevant(p):
        return p.endswith(SOURCE_SUFFIXES) or p.endswith(('.toml','.json','.yaml','.yml','.sh','.lock')) or Path(p).name in {'Makefile','go.mod','go.sum','go.work','go.work.sum','setup.cfg','.gitignore'}

    def safe_file(self,p):
        safe(p)
        if any(within(o,p) for o in self.opaque):raise ValueError('opaque source boundary')
        parts=Path(p).parts
        if len(parts)>LIMITS['depth']:raise ValueError('coverage depth limit')
        for i in range(1,len(parts)+1):
            ancestor=self.root/Path(*parts[:i])
            if ancestor.is_symlink():raise ValueError('symlink source boundary')
            if ancestor.is_dir() and (ancestor/'.git').exists():raise ValueError('nested repository/gitlink source boundary')

    def cached(self,key):
        data=self.cache.get(key)
        if data is not None:self.cache.move_to_end(key)
        return data

    def retain(self,key,data):
        old=self.cache.pop(key,None)
        if old is not None:self.retained_bytes-=len(old)
        # A valid per-file input need not fit the retained-byte allowance.
        if len(data)>LIMITS['bytes']:return
        while self.cache and self.retained_bytes+len(data)>LIMITS['bytes']:
            _,old=self.cache.popitem(last=False);self.retained_bytes-=len(old)
        self.cache[key]=data;self.retained_bytes+=len(data)
        self.peak_retained_bytes=max(self.peak_retained_bytes,self.retained_bytes)

    def read(self,p,charge=True):
        # Compatibility read API: the byte limit bounds retained payload, not the
        # aggregate size of independent ordinary source observations.
        data,_=self.read_observed(p)
        if charge:self.total+=len(data)
        return data

    def read_observed(self,p):
        self.safe_file(p)
        before=(self.root/p).lstat()
        allowance=LIMITS['file_bytes']
        if not stat.S_ISREG(before.st_mode):raise ValueError('nonregular coverage input')
        if before.st_size>allowance:raise ValueError('coverage byte limit')
        directory=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in Path(p).parts[:-1]:
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=child
            fd=os.open(Path(p).name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
        finally:os.close(directory)
        try:
            opened=os.fstat(fd)
            if file_identity(opened)!=file_identity(before):raise CaptureDrift('source-identity', [p])
            with os.fdopen(fd,'rb',closefd=False) as stream:data=stream.read(allowance+1)
            if len(data)>allowance:raise CaptureUnavailable('source-byte-limit', [p])
            if file_identity(os.fstat(fd))!=file_identity(opened) or file_identity((self.root/p).lstat())!=file_identity(before):raise CaptureDrift('source-identity', [p])
            return data,file_identity(opened)
        finally:os.close(fd)

    def observe_listing(self):
        progress.report('inventory-membership', count=0)
        result=set();exclusions=[];diagnostics=[];declarations=[];base_rows=[];priority_inputs=set()
        opaque_paths=set(self.opaque)
        def declaration_bytes(p):
            # Declarations are parsed one at a time, before ordinary content.
            # Their aggregate size cannot starve later priority manifests.
            data,identity=self.read_observed(p)
            declarations.append({'path':p,'sha256':hashlib.sha256(data).hexdigest(),'identity':list(identity)})
            return data
        def add(p,protected=False):
            p=p.rstrip('/')
            if not p or p=='.':return
            boundary=opaque_owner(opaque_paths,p)
            if boundary is not None:
                diagnostics.append(gap('opaque-boundary',boundary,'supplied opaque source boundary'));return
            try:self.safe_file(p)
            except (OSError,ValueError) as exc:
                diagnostics.append(gap('symlink-boundary' if 'symlink' in str(exc) else 'unsafe-boundary',p,str(exc)));return
            if not (self.root/p).exists():return
            parts=Path(p).parts
            if any(part in EXCLUDED for part in parts):
                if not protected:return
                exclusions.append({'path':p,'reason':'protected membership overrides generated exclusion','provenance':['tracked/base/explicit/declaration']})
            entries=['/'.join(parts[:i])+'/' for i in range(1,len(parts))]+[p+'/' if (self.root/p).is_dir() else p]
            for entry in entries:
                if entry in result:continue
                if len(result)>=LIMITS['files']:
                    diagnostics.append(gap('file-limit','.', 'coverage membership limit exceeded'));return
                result.add(entry)
                progress.report('inventory-membership', count=len(result))
        # Reserve root and supported member manifests before ordinary enumeration.
        roots=['.'];root_set={'.'};protected=set(self.paths)
        def add_root(p):
            if p in root_set or opaque_owner(opaque_paths,p) is not None:return
            if len(roots)>=LIMITS['files']:
                diagnostics.append(gap('file-limit','.', 'priority root membership limit'));return
            root_set.add(p);roots.append(p)
        def protect(p):
            if p not in protected and len(protected)>=LIMITS['files']:
                diagnostics.append(gap('file-limit','.', 'protected membership limit'));return
            protected.add(p)
        for boundary in self.opaque:add(boundary,True)
        tracked=[]
        try:
            tracked=[row.decode() for row in git_records(self.root,['ls-files','--cached','-z'],LIMITS)]
            for p in tracked:
                safe(p)
                if Path(p).name in MANIFESTS:add_root(Path(p).parent.as_posix())
        except (OSError,ValueError) as exc:diagnostics.append(gap('file-limit' if 'limit' in str(exc) else 'membership-unavailable','.',str(exc)))
        if self.base_oid:
            try:
                base_rows=git_records(self.root,['ls-tree','-r','-l','-z',self.base_oid],LIMITS)
                for row in base_rows:
                    meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split();p=path.decode();safe(p)
                    if mode==b'160000':diagnostics.append(gap('gitlink-boundary',p,'base gitlink is opaque'))
                    elif kind==b'blob' and Path(p).name in MANIFESTS:add_root(Path(p).parent.as_posix())
            except (OSError,ValueError) as exc:diagnostics.append(gap('base-unavailable','.',str(exc)))
        for kind in ('apps','services','packages'):
            try:
                observed=self.path_observation(kind)
                if observed['state']=='absent':continue
                if observed['state']=='opaque':
                    add(kind,True);continue
                if observed['state']!='directory':
                    diagnostics.append(gap('unsafe-boundary',kind,'priority container is not an ordinary directory'));continue
                with os.scandir(self.root/kind) as entries:
                    children=[]
                    for entry in entries:
                        if len(children)>=LIMITS['files']:raise ValueError('priority directory file-limit')
                        children.append(entry.name)
                for name in sorted(children):
                    p=kind+'/'+name
                    if name in EXCLUDED:continue
                    observed_child=self.path_observation(p)
                    if observed_child['state']=='opaque':add(p,True)
                    elif observed_child['state']=='directory':add_root(p);add(p)
            except FileNotFoundError:pass
            except (OSError,ValueError) as exc:diagnostics.append(gap('file-limit',kind,str(exc)))
        for p in self.paths:
            parts=Path(p).parts
            for i in range(1,len(parts)+1):
                ancestor=Path(*parts[:i]).as_posix()
                if self.path_observation(ancestor)['state']=='directory':add_root(ancestor)
        # Parse literal declarations as data, with the same pre-read bounds.
        visited=set()
        for directory in roots:
            if directory in visited:continue
            if len(visited)>=LIMITS['files']:
                diagnostics.append(gap('file-limit','.', 'priority declaration limit'));break
            visited.add(directory)
            for name in sorted(MANIFESTS):
                p=(Path(directory)/name).as_posix()
                if not (self.root/p).exists():continue
                add(p,True);priority_inputs.add(p)
                if name not in {'pyproject.toml','package.json','repository.json','pnpm-workspace.yaml'}:continue
                try:
                    raw=declaration_bytes(p).decode()
                    if name=='pnpm-workspace.yaml':
                        from al_ci_yaml import load
                        data=load(raw)
                    else:data=tomllib.loads(raw) if name=='pyproject.toml' else json.loads(raw)
                    declared=[]
                    if name=='pyproject.toml':
                        tool=data.get('tool',{});declared+=tool.get('uv',{}).get('workspace',{}).get('members',[])
                        setuptools=tool.get('setuptools',{});packages=setuptools.get('packages',{})
                        declared+=[(Path(directory)/v).as_posix() for v in (packages.get('find',{}).get('where',[]) if isinstance(packages,dict) else [])]
                        declared+=[(Path(directory)/v).as_posix() for v in setuptools.get('package-dir',{}).values()]
                    elif name=='package.json':
                        workspace=data.get('workspaces',[]);declared+=workspace.get('packages',[]) if isinstance(workspace,dict) else workspace
                    elif name=='pnpm-workspace.yaml':declared+=data.get('packages',[])
                    else:
                        declared+=[u['path'] for u in data.get('units',[])];declared+=data.get('containers',[])
                    for member in declared:
                        if isinstance(member,str) and not any(c in member for c in '*!$'):
                            safe(member);protect(member)
                            if member not in visited:add_root(member)
                except (OSError,ValueError,TypeError,AttributeError,KeyError) as exc:diagnostics.append(gap('unsupported-declaration',p,str(exc)))
        if (self.root/'.al/repository.json').exists():
            # Contract units may live below generated-looking literal roots.
            p='.al/repository.json';add(p,True);priority_inputs.add(p)
            try:
                data=json.loads(declaration_bytes(p))
                for u in data.get('units',[]):protect(safe(u['path']))
            except (OSError,ValueError,TypeError,KeyError) as exc:diagnostics.append(gap('unsupported-declaration',p,str(exc)))
        for p in tracked:add(p,True)
        for row in base_rows:
            try:
                meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split()
                if kind==b'blob':add(path.decode(),True)
            except (ValueError,OSError) as exc:diagnostics.append(gap('base-unavailable','.',str(exc)))
        opaque_excluded=literal_opaque_exclusions(opaque_paths)
        excluded=[v for name in sorted(EXCLUDED) for v in ('--exclude='+name+'/','--exclude=**/'+name+'/')]+opaque_excluded
        try:
            for row in git_records(self.root,['ls-files','--others','--exclude-per-directory=.gitignore',*excluded,'-z'],LIMITS):add(row.decode())
            for scope in sorted(protected):
                if scope=='.':continue
                add(scope,True)
                if opaque_owner(opaque_paths,scope) is not None:continue
                try:
                    self.safe_file(scope)
                    regular=stat.S_ISREG((self.root/scope).lstat().st_mode)
                except (OSError,ValueError):regular=False
                if regular:continue
                for row in git_records(self.root,['ls-files','--others',*opaque_excluded,'-z','--',scope],LIMITS):
                    p=row.decode();add(p,not any(part in EXCLUDED for part in Path(p).parts[len(Path(scope).parts):]))
        except (OSError,ValueError,UnicodeError) as exc:diagnostics.append(gap('file-limit' if 'limit' in str(exc) else 'membership-unavailable','.',str(exc)))
        # Bind repository ignore rules even when they exclude themselves.
        try:
            rules=git_records(self.root,['ls-files','--others','--cached','--exclude-per-directory=.gitignore',*excluded,'-z','--','.gitignore',':(glob)**/.gitignore'],LIMITS)
            for row in rules:add(row.decode(),True)
        except (OSError,ValueError) as exc:diagnostics.append(gap('membership-unavailable','.',str(exc)))
        try:
            ignored=git_records(self.root,['ls-files','--others','--ignored','--directory','--exclude-per-directory=.gitignore',*excluded,'-z'],LIMITS)
            for row in ignored:
                p=row.decode().rstrip('/');safe(p)
                if opaque_owner(opaque_paths,p) is not None:continue
                exclusions.append({'path':p,'reason':'repository ignore/generated output exclusion','provenance':sorted(v for v in result if Path(v).name=='.gitignore')+sorted(EXCLUDED)})
        except (OSError,ValueError) as exc:diagnostics.append(gap('membership-unavailable','.',str(exc)))
        exclusions.append({'path':'.','reason':'repository .gitignore and generated/cache directory exclusions','provenance':sorted(p for p in result if Path(p).name=='.gitignore')+sorted(EXCLUDED)})
        unique={digest(g):g for g in diagnostics}
        boundaries=[{'path':g['path'],'kind':g['code'],'observation':self.path_observation(g['path'])} for g in unique.values() if g['code'] in {'symlink-boundary','unsafe-boundary','gitlink-boundary'}]
        observation={'members':sorted(result),'exclusions':sorted(exclusions,key=lambda e:(e['path'],e['reason'])),'diagnostics':[unique[k] for k in sorted(unique)],'boundaries':boundaries,'declarations':declarations,'base_rows':[row.hex() for row in base_rows],'priority_inputs':sorted(priority_inputs),'complete':not diagnostics}
        progress.report('inventory-membership', count=len(result), force=True)
        return immutable_observation(observation)

    def install_listing(self, observation):
        self.members=list(observation['members'])
        self.gaps.extend(json.loads(json.dumps(observation['diagnostics'])))
        self.base_rows=[bytes.fromhex(row) for row in observation['base_rows']]
        self.priority_inputs=set(observation['priority_inputs'])
        self.membership_observation=json.loads(json.dumps({k:observation[k] for k in ('exclusions','complete','boundaries')}))
        self.membership_observation['base_rows_sha256']=digest(observation['base_rows'])
        for row in observation['declarations']:
            self.observe_path(row['path'])
            data,identity=self.read_observed(row['path'])
            if tuple(identity)!=tuple(row['identity']) or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise CaptureDrift('declaration-content', [row['path']])

    def listing(self):
        return list(self.observe_listing()['members'])

    def base_batches(self,paths):
        batch=[];total=0
        for path in paths:
            size=self.base.records[path]['size']
            if batch and total+size>self.batch_bytes:
                yield batch;batch=[];total=0
            batch.append(path);total+=size
        if batch:yield batch

    @measured('base-enumeration')
    def read_base(self):
        if not self.base_oid:return
        progress.report('inventory-base', count=0)
        try:
            for row in sorted(self.base_rows,key=lambda row:(row.split(b'\t',1)[1].decode() not in self.priority_inputs,priority(row.split(b'\t',1)[1].decode()))):
                meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split();p=path.decode()
                if kind!=b'blob' or not self.relevant(p) or any(within(o,p) for o in self.opaque):continue
                if mode==b'120000':self.gaps.append(gap('base-symlink',p,'base source is a symlink'));continue
                if int(size)>LIMITS['file_bytes']:
                    self.gaps.append(gap('base-byte-limit',p,'base per-file content byte limit'));continue
                self.base.records[p]={'oid':oid.decode(),'size':int(size)};self.base_modes[p]=mode.decode()
            from al_git_cache import blobs
            for batch in self.base_batches(list(self.base)):
                try:
                    values=blobs(self.root,[self.base.records[p]['oid'] for p in batch],max_bytes=self.batch_bytes,file_bytes=LIMITS['file_bytes'])
                    for p in batch:
                        data=values[self.base.records[p]['oid']]
                        self.base.records[p]['sha256']=hashlib.sha256(data).hexdigest();self.retain(('base',p),data)
                except (OSError,ValueError,subprocess.CalledProcessError) as exc:
                    for p in batch:
                        del self.base.records[p];self.base_modes.pop(p,None)
                        self.gaps.append(gap('base-unavailable',p,str(exc)))
                progress.report('inventory-base', count=len(self.base))
            progress.report('inventory-base', count=len(self.base), force=True)
        except (OSError,ValueError,subprocess.CalledProcessError) as exc:self.gaps.append(gap('base-unavailable','.',str(exc)))

    def text(self,p,required=False,rule=False):
        try:return self.capture_bytes(p,required=required,rule=rule).decode('utf-8')
        except CaptureDrift:raise
        except (OSError,ValueError,UnicodeError) as exc:
            self.gaps.append(gap('unreadable-input',p,str(exc)))
            return ''

    def stable(self):
        try:
            observation=self.observe_listing()
            diagnostics=json.loads(json.dumps(observation['diagnostics']))
            self.gaps.extend(row for row in diagnostics if row not in self.gaps)
            self.revalidate(observation);return True
        except (CaptureDrift,CaptureUnavailable,OSError,ValueError):return False


def membership_digest(members):
    # Non-source marker bytes still bind execution evidence, but do not change discovered units.
    return digest([p for p in members if p.endswith('/') or Snapshot.relevant(p)])


@measured('discovery')
def collect(root,*,base_oid,paths,opaque,required_adapter=None):
    return capture_discovery(lambda:Snapshot(root,base_oid,paths,opaque,required_adapter),collect_from_snapshot)


def collect_from_snapshot(scan):
    import al_repo_adapters as adapters
    import al_repo_dependencies as imports
    progress.report('inventory-analysis')
    adapter=adapters.select(scan);facts=adapter.observe(scan)
    facts.update(schema_version=2,repository={'root':str(scan.root),'base_oid':scan.base_oid},adapter={'id':adapter.ID,'version':adapter.VERSION},policy_version=2)
    facts['enumeration']['gaps'].extend(scan.gaps)
    facts['enumeration']['exclusions'].extend(e for e in scan.membership_observation['exclusions'] if e['reason']!='repository ignore/generated output exclusion')
    facts['enumeration']['ignored_outputs']=[e for e in scan.membership_observation['exclusions'] if e['reason']=='repository ignore/generated output exclusion']
    facts['enumeration']['membership_sha256']=membership_digest(scan.members)
    facts['enumeration']['membership_rules']=sorted(p for p in scan.files if Path(p).name=='.gitignore')
    facts['dependencies']=imports.analyze(facts,scan)
    facts['unsupported_boundaries']=[gap('opaque-boundary',p,'opaque child content cannot be enumerated') for p in sorted(scan.opaque)]
    facts['rule_sources']=[{'path':p,'sha256':scan.files.records[p]['sha256']} for p in sorted(scan.rules) if p in scan.files]
    facts['scan_inputs']=[{'path':p,'snapshot':which,'sha256':record['sha256']} for which,files in [('base',scan.base),('current',scan.files)] for p,record in sorted(files.records.items()) if p.endswith(SOURCE_SUFFIXES) and any((p.endswith('.py') and any(within(r,p) for r in u['source_roots'])) or (not p.endswith('.py') and within(u['path'],p)) for u in facts['units'])]
    facts['enumeration']['absent_paths']=sorted(scan.absent)
    facts['scan']={'stable':True,'limits':LIMITS,'membership_digest':digest(scan.members),'provenance_digest':digest(facts['scan_inputs'])}
    selected=set(facts['dependencies']['potential_services']) if facts['dependencies']['uncertainties'] else set(facts['dependencies']['known_affected'])|set(facts['dependencies'].get('policy_selection',[]))
    facts['dependencies']['coverage_inputs']=sorted({row[1] for row in facts['dependencies']['source_membership'] if row[2] in selected}|set(facts['dependencies'].get('affected_source_paths',[])))
    facts['question']={'changed_paths':scan.paths,'directory_paths':sorted(p for p in scan.paths if scan.observe_path(p)['state']=='directory')}
    facts['capture']=scan.capture_metadata()
    facts['dependencies']['boundary_consumers']={p:imports._referencing(scan,p,p) for p in sorted(set(scan.opaque)|{g['path'] for g in scan.gaps if g['code'] in {'symlink-boundary','unsafe-boundary','gitlink-boundary'}})}
    validate_facts(facts);return facts


def validate_facts(facts):
    if (facts.get('schema_version'),facts.get('policy_version')) not in {(1,1),(2,2)}:raise ValueError('unsupported repository fact version')
    adapters=({'id':'python','version':1},{'id':'contract','version':1})
    if facts['schema_version']==2:adapters+=({'id':'conventional','version':1},{'id':'contract','version':2})
    if facts.get('adapter') not in adapters:raise ValueError('unsupported repository adapter')
    if not isinstance(facts.get('repository'),dict) or not isinstance(facts['repository'].get('root'),str) or not Path(facts['repository']['root']).is_absolute():raise ValueError('invalid repository identity')
    count=0
    def bounded(value,depth=0):
        nonlocal count
        count+=1
        if count>2000000 or depth>32:raise ValueError('coverage fact complexity limit')
        if isinstance(value,str) and len(value)>65536:raise ValueError('coverage string limit')
        if isinstance(value,list):
            if len(value)>100000:raise ValueError('coverage collection limit')
            for item in value:bounded(item,depth+1)
        elif isinstance(value,dict):
            if len(value)>10000:raise ValueError('coverage mapping limit')
            for key,item in value.items():
                if not isinstance(key,str):raise ValueError('invalid coverage field')
                bounded(item,depth+1)
        elif value is not None and not isinstance(value,(str,int,bool)):raise ValueError('invalid coverage value')
    bounded(facts)
    if not isinstance(facts.get('units'),list):raise ValueError('repository units required')
    if len({u['id'] for u in facts['units']})!=len(facts['units']):raise ValueError('duplicate repository unit')
    for u in facts['units']:
        if u['id']!=u['path']:raise ValueError('unit identity must match canonical path')
        safe(u['path'])
        if 'go' in u and facts['adapter'] not in ({'id':'conventional','version':1},{'id':'contract','version':2}):raise ValueError('Go module facts require a current composed adapter')
        for root in u['source_roots']:safe(root)
    for category in ('obligations','bindings','gaps'):
        if not isinstance(facts['checks'][category],list):raise ValueError('invalid check observations')
    ids=[b['id'] for b in facts['checks']['bindings']]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate check identity')
    obligations=[o['id'] for o in facts['checks']['obligations']]
    if len(obligations)!=len(set(obligations)):raise ValueError('duplicate obligation identity')
    for binding in facts['checks']['bindings']:
        if binding['execution_signature']!=digest({k:v for k,v in binding.items() if k!='execution_signature'}):raise ValueError('invalid check execution signature')
        for path in binding['required_scope']:safe(path)
        for source in binding['source_chain']:safe(source['path'])
        if not set(binding['obligation_ids'])<=set(obligations):raise ValueError('foreign check obligation')
    for key in ('known_affected','potential_services'):
        if not set(facts['dependencies'][key])<={u['id'] for u in facts['units']}:raise ValueError('foreign dependency unit')
    if not isinstance(facts['scan']['stable'],bool):raise ValueError('invalid scan stability')


def semantic_requirements(facts):
    deps=facts['dependencies']
    value= {'schema_version':facts['schema_version'],'repository':facts['repository'],'adapter':facts['adapter'],'policy_version':facts['policy_version'],
            'units':facts['units'],'enumeration':{k:v for k,v in facts['enumeration'].items() if k not in {'absent_paths','ignored_outputs'}},
            # Import edges and source membership change the requirements hash through digests, not copies.
            'dependencies':{**{k:v for k,v in deps.items() if k not in {'edges','source_membership'}},
                            'edges_sha256':digest(deps['edges']),'source_membership_sha256':digest(deps['source_membership']),
                            'uncertainties':[{k:v for k,v in u.items() if k!='location'} for u in deps['uncertainties']]},
            'question':facts.get('question',{}),'ci_observations':facts.get('ci_observations',{}),'rule_sources':facts.get('rule_sources',[]),'checks':facts['checks'],'unsupported_boundaries':facts['unsupported_boundaries'],'membership_rules':[r for r in facts.get('rule_sources',[]) if r['path'] in facts['enumeration'].get('membership_rules',[])],'stable':facts['scan']['stable']}
    if facts['schema_version']==1:
        for key in ('question','ci_observations','rule_sources'):value.pop(key,None)
    return value
