"""Bounded read-only snapshots and semantic coverage provenance."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import selectors
import stat
import time
import tomllib
from al_profile import measured

SOURCE_SUFFIXES=('.py','.js','.jsx','.ts','.tsx','.go','.rs','.java','.kt','.c','.cc','.cpp','.rb','.php','.swift','.cs')
LIMITS={'files':50000,'bytes':64*1024*1024,'file_bytes':2*1024*1024,'depth':32}
EXCLUDED={'.git','.codex','.claude','.venv','node_modules','__pycache__','.pytest_cache','.ruff_cache','dist','build','.next','.turbo'}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def safe(path):
    p=Path(path)
    if not isinstance(path,str) or not path or p.is_absolute() or '..' in p.parts or p.as_posix()!=path:raise ValueError('unsafe coverage path: '+str(path))
    return path


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


MANIFESTS={'pyproject.toml','package.json','pnpm-workspace.yaml','go.mod','setup.cfg','setup.py','Cargo.toml','.gitignore','repository.json'}


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


class Snapshot:
    def __init__(self,root,base_oid,paths,opaque,required_adapter=None):
        if any(type(v) is not int or v<=0 for v in LIMITS.values()):raise ValueError('invalid coverage limits')
        self.required_adapter=required_adapter;self.root=Path(root).resolve();self.base_oid=base_oid
        self.paths=[];path_overflow=False
        for path in paths:
            if len(self.paths)>=LIMITS['files']:path_overflow=True;break
            self.paths.append(safe(path))
        self.paths=sorted(set(self.paths));self.opaque=sorted(opaque)
        self.gaps=[];self.files={};self.base={};self.modes={};self.base_modes={};self.rules=set();self.absent=set();self.total=0
        if path_overflow:self.gaps.append(gap('file-limit','.', 'explicit path membership limit'))
        if any(type(v) is not int or v<=0 for v in LIMITS.values()):raise ValueError('invalid coverage limits')
        self.members=self.listing()
        for p in sorted(self.members,key=lambda p:(p not in self.priority_inputs,priority(p))):
            if not p.endswith('/') and self.relevant(p) and (self.root/p).exists():
                try:
                    data=self.read(p)
                    self.files[p]=data;self.modes[p]=(self.root/p).lstat().st_mode&0o7777
                    if Path(p).name=='.gitignore':self.rules.add(p)
                except (OSError,ValueError) as exc:self.gaps.append(gap('unreadable-input',p,str(exc)))
        self.read_base()

    @staticmethod
    def relevant(p):
        return p.endswith(SOURCE_SUFFIXES) or p.endswith(('.toml','.json','.yaml','.yml','.sh','.lock')) or Path(p).name in {'Makefile','go.mod','setup.cfg','.gitignore'}

    def safe_file(self,p):
        safe(p)
        if any(within(o,p) for o in self.opaque):raise ValueError('opaque source boundary')
        parts=Path(p).parts
        if len(parts)>LIMITS['depth']:raise ValueError('coverage depth limit')
        for i in range(1,len(parts)+1):
            ancestor=self.root/Path(*parts[:i])
            if ancestor.is_symlink():raise ValueError('symlink source boundary')
            if ancestor.is_dir() and (ancestor/'.git').exists():raise ValueError('nested repository/gitlink source boundary')

    def read(self,p,charge=True):
        self.safe_file(p)
        before=(self.root/p).lstat()
        allowance=min(LIMITS['file_bytes'],LIMITS['bytes']-self.total if charge else LIMITS['file_bytes'])
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
            if file_identity(opened)!=file_identity(before):raise ValueError('coverage input identity changed')
            with os.fdopen(fd,'rb',closefd=False) as stream:data=stream.read(allowance+1)
            if charge:self.total+=len(data)
            if len(data)>allowance or file_identity(os.fstat(fd))!=file_identity(opened) or file_identity((self.root/p).lstat())!=file_identity(before):raise ValueError('coverage input changed/byte limit')
            return data
        finally:os.close(fd)

    def listing(self):
        result=set();exclusions=[];before_gaps=len(self.gaps);base_rows=[];rule_bytes=0;priority_inputs=set()
        def declaration_bytes(p):
            nonlocal rule_bytes
            size=(self.root/p).lstat().st_size
            if rule_bytes+size>LIMITS['bytes']:raise ValueError('declaration aggregate byte limit')
            data=self.read(p,False);rule_bytes+=len(data);return data
        def add(p,protected=False):
            p=p.rstrip('/')
            if not p or p=='.':return
            try:self.safe_file(p)
            except (OSError,ValueError) as exc:
                self.gaps.append(gap('symlink-boundary' if 'symlink' in str(exc) else 'unsafe-boundary',p,str(exc)));return
            if not (self.root/p).exists():return
            parts=Path(p).parts
            if any(part in EXCLUDED for part in parts):
                if not protected:return
                exclusions.append({'path':p,'reason':'protected membership overrides generated exclusion','provenance':['tracked/base/explicit/declaration']})
            entries=['/'.join(parts[:i])+'/' for i in range(1,len(parts))]+[p+'/' if (self.root/p).is_dir() else p]
            for entry in entries:
                if entry in result:continue
                if len(result)>=LIMITS['files']:
                    self.gaps.append(gap('file-limit','.', 'coverage membership limit exceeded'));return
                result.add(entry)
        # Reserve root and supported member manifests before ordinary enumeration.
        roots=['.'];root_set={'.'};protected=set(self.paths)
        def add_root(p):
            if p in root_set:return
            if len(roots)>=LIMITS['files']:
                self.gaps.append(gap('file-limit','.', 'priority root membership limit'));return
            root_set.add(p);roots.append(p)
        def protect(p):
            if p not in protected and len(protected)>=LIMITS['files']:
                self.gaps.append(gap('file-limit','.', 'protected membership limit'));return
            protected.add(p)
        tracked=[]
        try:
            tracked=[row.decode() for row in git_records(self.root,['ls-files','--cached','-z'],LIMITS)]
            for p in tracked:
                safe(p)
                if Path(p).name in MANIFESTS:add_root(Path(p).parent.as_posix())
        except (OSError,ValueError) as exc:self.gaps.append(gap('file-limit' if 'limit' in str(exc) else 'membership-unavailable','.',str(exc)))
        if self.base_oid:
            try:
                base_rows=git_records(self.root,['ls-tree','-r','-l','-z',self.base_oid],LIMITS)
                for row in base_rows:
                    meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split();p=path.decode();safe(p)
                    if mode==b'160000':self.gaps.append(gap('gitlink-boundary',p,'base gitlink is opaque'))
                    elif kind==b'blob' and Path(p).name in MANIFESTS:add_root(Path(p).parent.as_posix())
            except (OSError,ValueError) as exc:self.gaps.append(gap('base-unavailable','.',str(exc)))
        for kind in ('apps','services','packages'):
            try:
                with os.scandir(self.root/kind) as entries:
                    children=[]
                    for entry in entries:
                        if len(children)>=LIMITS['files']:raise ValueError('priority directory file-limit')
                        children.append(entry.name)
                for name in sorted(children):
                    p=kind+'/'+name
                    if name in EXCLUDED:continue
                    if (self.root/p).is_symlink():add(p,True)
                    elif (self.root/p).is_dir():add_root(p);add(p)
            except FileNotFoundError:pass
            except (OSError,ValueError) as exc:self.gaps.append(gap('file-limit',kind,str(exc)))
        for p in self.paths:
            parts=Path(p).parts
            for i in range(1,len(parts)+1):
                if (self.root/Path(*parts[:i])).is_dir():add_root('/'.join(parts[:i]))
        # Parse literal declarations as data, with the same pre-read bounds.
        visited=set()
        for directory in roots:
            if directory in visited:continue
            if len(visited)>=LIMITS['files']:
                self.gaps.append(gap('file-limit','.', 'priority declaration limit'));break
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
                except (OSError,ValueError,TypeError,AttributeError,KeyError) as exc:self.gaps.append(gap('unsupported-declaration',p,str(exc)))
        if (self.root/'.al/repository.json').exists():
            # Contract units may live below generated-looking literal roots.
            p='.al/repository.json';add(p,True);priority_inputs.add(p)
            try:
                data=json.loads(declaration_bytes(p))
                for u in data.get('units',[]):protect(safe(u['path']))
            except (OSError,ValueError,TypeError,KeyError) as exc:self.gaps.append(gap('unsupported-declaration',p,str(exc)))
        for p in tracked:add(p,True)
        for row in base_rows:
            try:
                meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split()
                if kind==b'blob':add(path.decode(),True)
            except (ValueError,OSError) as exc:self.gaps.append(gap('base-unavailable','.',str(exc)))
        excluded=[v for name in sorted(EXCLUDED) for v in ('--exclude='+name+'/','--exclude=**/'+name+'/')]
        try:
            for row in git_records(self.root,['ls-files','--others','--exclude-per-directory=.gitignore',*excluded,'-z'],LIMITS):add(row.decode())
            for scope in sorted(protected):
                if scope=='.':continue
                add(scope,True)
                for row in git_records(self.root,['ls-files','--others','-z','--',scope],LIMITS):
                    p=row.decode();add(p,not any(part in EXCLUDED for part in Path(p).parts[len(Path(scope).parts):]))
        except (OSError,ValueError,UnicodeError) as exc:self.gaps.append(gap('file-limit' if 'limit' in str(exc) else 'membership-unavailable','.',str(exc)))
        # Bind repository ignore rules even when they exclude themselves.
        try:
            rules=git_records(self.root,['ls-files','--others','--cached','--exclude-per-directory=.gitignore',*excluded,'-z','--','.gitignore',':(glob)**/.gitignore'],LIMITS)
            for row in rules:add(row.decode(),True)
        except (OSError,ValueError) as exc:self.gaps.append(gap('membership-unavailable','.',str(exc)))
        try:
            ignored=git_records(self.root,['ls-files','--others','--ignored','--directory','--exclude-per-directory=.gitignore',*excluded,'-z'],LIMITS)
            for row in ignored:
                p=row.decode().rstrip('/');safe(p)
                exclusions.append({'path':p,'reason':'repository ignore/generated output exclusion','provenance':sorted(v for v in result if Path(v).name=='.gitignore')+sorted(EXCLUDED)})
        except (OSError,ValueError) as exc:self.gaps.append(gap('membership-unavailable','.',str(exc)))
        exclusions.append({'path':'.','reason':'repository .gitignore and generated/cache directory exclusions','provenance':sorted(p for p in result if Path(p).name=='.gitignore')+sorted(EXCLUDED)})
        self.membership_observation={'exclusions':sorted(exclusions,key=lambda e:(e['path'],e['reason'])),'base_rows_sha256':digest([r.hex() for r in base_rows]),'complete':len(self.gaps)==before_gaps}
        self.base_rows=base_rows;self.priority_inputs=priority_inputs
        return sorted(result)

    @measured('base-enumeration')
    def read_base(self):
        if not self.base_oid:return
        try:
            rows=[];total=0
            for row in sorted(self.base_rows,key=lambda row:(row.split(b'\t',1)[1].decode() not in self.priority_inputs,priority(row.split(b'\t',1)[1].decode()))):
                meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split();p=path.decode()
                if kind!=b'blob' or not self.relevant(p) or any(within(o,p) for o in self.opaque):continue
                if mode==b'120000':self.gaps.append(gap('base-symlink',p,'base source is a symlink'));continue
                if int(size)>LIMITS['file_bytes'] or total+int(size)>LIMITS['bytes']:
                    self.gaps.append(gap('base-byte-limit',p,'base content byte limit'));continue
                total+=int(size);rows.append((p,oid));self.base_modes[p]=mode.decode()
            from al_git_cache import blobs
            values=blobs(self.root,[oid.decode() for _,oid in rows],max_bytes=LIMITS['bytes'],file_bytes=LIMITS['file_bytes'])
            self.base={p:values[oid.decode()] for p,oid in rows}
        except (OSError,ValueError,subprocess.CalledProcessError) as exc:self.gaps.append(gap('base-unavailable','.',str(exc)))

    def text(self,p,required=False,rule=False):
        safe(p)
        if rule:self.rules.add(p)
        if p not in self.files:
            self.absent.add(p)
            if required:self.gaps.append(gap('missing-input',p,'required repository input is absent'))
            return ''
        try:return self.files[p].decode('utf-8')
        except UnicodeError:
            self.gaps.append(gap('invalid-encoding',p,'required source is not UTF-8'));return ''

    def stable(self):
        observation=self.membership_observation;count=len(self.gaps)
        if self.listing()!=self.members or observation!=self.membership_observation or len(self.gaps)!=count:return False
        try:return all(self.read(p,False)==data for p,data in self.files.items()) and all(not (self.root/p).exists() for p in self.absent)
        except (OSError,ValueError):return False


def membership_digest(members):
    # Non-source marker bytes still bind execution evidence, but do not change discovered units.
    return digest([p for p in members if p.endswith('/') or Snapshot.relevant(p)])


@measured('discovery')
def collect(root,*,base_oid,paths,opaque,required_adapter=None):
    import al_repo_adapters as adapters
    import al_python_imports as imports
    scan=Snapshot(root,base_oid,paths,opaque,required_adapter);adapter=adapters.select(scan);facts=adapter.observe(scan)
    facts.update(schema_version=1,repository={'root':str(scan.root),'base_oid':base_oid},adapter={'id':adapter.ID,'version':adapter.VERSION},policy_version=1)
    facts['enumeration']['gaps'].extend(scan.gaps)
    facts['enumeration']['exclusions'].extend(e for e in scan.membership_observation['exclusions'] if e['reason']!='repository ignore/generated output exclusion')
    facts['enumeration']['ignored_outputs']=[e for e in scan.membership_observation['exclusions'] if e['reason']=='repository ignore/generated output exclusion']
    facts['enumeration']['membership_sha256']=membership_digest(scan.members)
    facts['enumeration']['membership_rules']=sorted(p for p in scan.files if Path(p).name=='.gitignore')
    facts['dependencies']=imports.analyze(facts,scan)
    facts['unsupported_boundaries']=[gap('opaque-boundary',p,'opaque child content cannot be enumerated') for p in sorted(opaque)]
    facts['rule_sources']=[{'path':p,'sha256':hashlib.sha256(scan.files[p]).hexdigest()} for p in sorted(scan.rules) if p in scan.files]
    facts['scan_inputs']=[{'path':p,'snapshot':which,'sha256':hashlib.sha256(data).hexdigest()} for which,files in [('base',scan.base),('current',scan.files)] for p,data in sorted(files.items()) if p.endswith(SOURCE_SUFFIXES) and any((p.endswith('.py') and any(within(r,p) for r in u['source_roots'])) or (not p.endswith('.py') and within(u['path'],p)) for u in facts['units'])]
    facts['enumeration']['absent_paths']=sorted(scan.absent)
    facts['scan']={'stable':scan.stable(),'limits':LIMITS,'membership_digest':digest(scan.members),'provenance_digest':digest(facts['scan_inputs'])}
    validate_facts(facts);return facts


def validate_facts(facts):
    if facts.get('schema_version')!=1 or facts.get('policy_version')!=1:raise ValueError('unsupported repository fact version')
    if facts.get('adapter') not in ({'id':'python','version':1},{'id':'contract','version':1}):raise ValueError('unsupported repository adapter')
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
    return {'schema_version':1,'repository':facts['repository'],'adapter':facts['adapter'],'policy_version':facts['policy_version'],
            'units':facts['units'],'enumeration':{k:v for k,v in facts['enumeration'].items() if k not in {'absent_paths','ignored_outputs'}},
            # Import edges and source membership change the requirements hash through digests, not copies.
            'dependencies':{**{k:v for k,v in deps.items() if k not in {'edges','source_membership'}},
                            'edges_sha256':digest(deps['edges']),'source_membership_sha256':digest(deps['source_membership']),
                            'uncertainties':[{k:v for k,v in u.items() if k!='location'} for u in deps['uncertainties']]},
            'checks':facts['checks'],'unsupported_boundaries':facts['unsupported_boundaries'],'membership_rules':[r for r in facts.get('rule_sources',[]) if r['path'] in facts['enumeration'].get('membership_rules',[])],'stable':facts['scan']['stable']}
