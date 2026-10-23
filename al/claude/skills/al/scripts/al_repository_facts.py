"""Bounded read-only snapshots and semantic coverage provenance."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
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


def gap(code,path,reason,unit=None,category=None):
    return dict(code=code,path=path,reason=reason,unit=unit,**({'category':category} if category else {}))


class Snapshot:
    def __init__(self,root,base_oid,paths,opaque,required_adapter=None):
        self.required_adapter=required_adapter;self.root=Path(root).resolve();self.base_oid=base_oid;self.paths=sorted(set(map(safe,paths)));self.opaque=sorted(opaque)
        self.gaps=[];self.files={};self.base={};self.modes={};self.base_modes={};self.rules=set();self.absent=set();self.total=0
        self.members=self.listing()
        for p in self.members:
            if not p.endswith('/') and self.relevant(p):
                try:
                    data=(self.root/p).read_bytes();self.total+=len(data)
                    if len(data)>LIMITS['file_bytes'] or self.total>LIMITS['bytes']:raise ValueError('coverage byte limit')
                    self.files[p]=data;self.modes[p]=(self.root/p).stat().st_mode&0o7777
                except (OSError,ValueError) as exc:self.gaps.append(gap('unreadable-input',p,str(exc)))
        self.read_base()

    @staticmethod
    def relevant(p):
        return p.endswith(SOURCE_SUFFIXES) or p.endswith(('.toml','.json','.yaml','.yml','.sh','.lock')) or Path(p).name in {'Makefile','go.mod','setup.cfg'}

    def listing(self):
        result=[]
        def error(exc):self.gaps.append(gap('unreadable-directory',str(exc.filename),str(exc)))
        for directory,dirs,files in os.walk(self.root,followlinks=False,onerror=error):
            rel=Path(directory).relative_to(self.root)
            if len(rel.parts)>LIMITS['depth']:
                self.gaps.append(gap('depth-limit',rel.as_posix(),'coverage traversal depth exceeded'));dirs[:]=[];continue
            keep=[]
            for name in sorted(dirs):
                p=(rel/name).as_posix()
                if name in EXCLUDED:continue
                if any(within(o,p) for o in self.opaque):continue
                if (self.root/p).is_symlink():self.gaps.append(gap('symlink-boundary',p,'symlink directory cannot prove source membership'));continue
                result.append(p+'/');keep.append(name)
            dirs[:]=keep
            for name in sorted(files):
                p=(rel/name).as_posix()
                if any(within(o,p) for o in self.opaque):continue
                if (self.root/p).is_symlink():
                    if self.relevant(p):self.gaps.append(gap('symlink-boundary',p,'symlink source cannot establish identity'))
                    continue
                result.append(p)
            if len(result)>LIMITS['files']:
                self.gaps.append(gap('file-limit','.', 'coverage membership limit exceeded'));break
        return sorted(result)

    @measured('base-enumeration')
    def read_base(self):
        if not self.base_oid:return
        try:
            result=subprocess.run(['git','ls-tree','-rlz','--full-tree',self.base_oid],cwd=self.root,capture_output=True,check=True)
            rows=[];total=0
            for row in filter(None,result.stdout.split(b'\0')):
                meta,path=row.split(b'\t',1);mode,kind,oid,size=meta.split();p=path.decode()
                if kind==b'blob' and self.relevant(p) and not any(part in EXCLUDED for part in Path(p).parts) and not any(within(o,p) for o in self.opaque):
                    total+=int(size)
                    if int(size)>LIMITS['file_bytes'] or total>LIMITS['bytes']:raise ValueError('base byte limit')
                    if mode==b'120000':self.gaps.append(gap('base-symlink',p,'base source is a symlink'));continue
                    rows.append((p,oid));self.base_modes[p]=mode.decode()
            if len(rows)>LIMITS['files']:raise ValueError('base membership limit')
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
        if self.listing()!=self.members:return False
        try:
            return all((self.root/p).read_bytes()==data for p,data in self.files.items()) and all(not (self.root/p).exists() for p in self.absent)
        except OSError:return False


@measured('discovery')
def collect(root,*,base_oid,paths,opaque,required_adapter=None):
    import al_repo_adapters as adapters
    import al_python_imports as imports
    scan=Snapshot(root,base_oid,paths,opaque,required_adapter);adapter=adapters.select(scan);facts=adapter.observe(scan)
    facts.update(schema_version=1,repository={'root':str(scan.root),'base_oid':base_oid},adapter={'id':adapter.ID,'version':adapter.VERSION},policy_version=1)
    facts['enumeration']['gaps'].extend(scan.gaps)
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
        if count>500000 or depth>32:raise ValueError('coverage fact complexity limit')
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
            'units':facts['units'],'enumeration':{k:v for k,v in facts['enumeration'].items() if k!='absent_paths'},
            'dependencies':{**deps,'uncertainties':[{k:v for k,v in u.items() if k!='location'} for u in deps['uncertainties']]},
            'checks':facts['checks'],'unsupported_boundaries':facts['unsupported_boundaries'],'stable':facts['scan']['stable']}
