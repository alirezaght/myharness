"""Closed local stdlib checks; never reproduces a remote environment lifecycle."""
import hashlib
import os
from pathlib import Path,PurePosixPath
import shlex
import stat
import sys
from al_ci_yaml import Unsupported
import al_ci_verification as V


def interpreter():
    requested=Path(sys.executable)
    if not requested.is_absolute():raise Unsupported('trusted interpreter is not absolute')
    try:path=requested.resolve(strict=True);before=path.stat();raw=path.read_bytes();after=path.stat()
    except (OSError,ValueError):raise Unsupported('trusted environment-check interpreter unavailable') from None
    fields=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if not stat.S_ISREG(before.st_mode) or not os.access(path,os.X_OK) or fields(before)!=fields(after):raise Unsupported('trusted interpreter changed or is not executable')
    return {'requested':str(requested),'executable':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'mode':stat.S_IMODE(before.st_mode)}


def target(root,name,opaque):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or p.as_posix()!=name or any(c in name for c in '*?[]$`\\;|<>'):
        raise Unsupported('unsafe environment-check target')
    path=root/name
    if not path.exists() or any(v.is_symlink() for v in (path,*path.parents)):raise Unsupported('missing/unsafe environment-check target')
    if any(name=='.' or name==child or name.startswith(child+'/') or child.startswith(name+'/') for child in opaque):raise Unsupported('opaque environment-check input')
    count=0
    if path.is_dir():
        def unavailable(error):raise Unsupported('environment-check input cannot be enumerated')
        for parent,dirs,files in os.walk(path,followlinks=False,onerror=unavailable):
            count+=len(dirs)+len(files)
            if count>50000:raise Unsupported('environment-check input observation limit')
            if any((Path(parent)/v).is_symlink() for v in [*dirs,*files]):raise Unsupported('unsafe symlink environment-check input')
    elif not path.is_file():raise Unsupported('unsupported environment-check input')
    return {'path':name,'kind':'directory' if path.is_dir() else 'file'}


def execution(job,root,opaque=(),global_context=(),*,capture=None):
    allowed={'stage','environment','script','before_script','rules','needs'}
    if set(job)-allowed or global_context:raise Unsupported('unsupported environment-dependent execution settings')
    if job.get('before_script') not in (None,[], ''):raise Unsupported('unsupported environment-check setup')
    if job.get('needs'):raise Unsupported('unavailable environment-check dependency inputs')
    if any(isinstance(r,dict) and (set(r)&{'variables','needs'} or r.get('when') in {'manual','delayed'}) for r in job.get('rules',[])):
        raise Unsupported('unsupported environment-check rule execution context')
    environment=job['environment']
    if not isinstance(environment,dict) or set(environment)!={'name','action'} or environment['action'] not in {'prepare','access'}:
        raise Unsupported('unsupported environment-check metadata')
    lines=job.get('script',[]);lines=[lines] if isinstance(lines,str) else lines
    if not isinstance(lines,list) or not lines or any(not isinstance(line,str) or not line.strip() for line in lines):raise Unsupported('missing environment verification checks')
    from al_go_checks import command_candidate
    if command_candidate(lines,require_all=True):
        from al_go_checks import execution as go_execution
        from al_ci_github import read
        def go_read(path):
            if any(path==o or path.startswith(o+'/') for o in opaque):raise Unsupported('opaque Go recipe input')
            return capture.capture_bytes(path,required=True,rule=True) if capture else read(root,path)[0]
        result=go_execution(lines,root,go_read,observe=capture.observe_path if capture else None)
        result['verification_chain']['environment_profile']={'schema_version':1,'profile':'literal-go-environment-checks-v1',
            'environment':environment,'setup_policy':'absent-or-empty-before-script; no external execution inputs',
            'local_environment_policy':'toolchain prerequisite verified by actual evidence; remote lifecycle not reproduced'}
        return result
    pin=interpreter();requirements=[];checks=[];effective=[];inputs=[]
    for index,line in enumerate(lines):
        if any(c in line for c in ('$', '`',';','&','|','>','<','\\','\n','\r')):raise Unsupported('unsupported environment-check expansion/control flow')
        try:tokens=shlex.split(line)
        except ValueError:raise Unsupported('malformed environment-check command') from None
        if len(tokens)<4 or tokens[0] not in {'python','python3'} or tokens[1]!='-m':raise Unsupported('unsupported environment verification command')
        module=tokens[2];args=tokens[3:]
        if module=='compileall':
            names=[v for v in args if not v.startswith('-')]
            if not names or any(v.startswith('-') and v not in {'-q','-qq'} for v in args):raise Unsupported('unsupported syntax-check options/targets')
        elif module=='json.tool':
            if len(args)!=1 or args[0].startswith('-'):raise Unsupported('unsupported JSON-check options/output')
            names=args
        else:raise Unsupported('unsupported environment verification module')
        selected=[target(root,name,opaque) for name in names]
        if module=='json.tool' and selected[0]['kind']!='file':raise Unsupported('JSON-check requires an input file')
        argv=[pin['executable'],'-I','-S','-m',module,*args];records=V.observed(tokens)
        if not records:raise Unsupported('unsupported environment verification command')
        requirements.append({'selector':'script['+str(index)+']','purpose':'verification','original_argv':tokens,'argv':argv,
                             'cwd':'.','env_names':['PATH'],'targets':records,'input_scope':selected,'interpreter':pin})
        checks.extend(records);effective.append(argv);inputs.extend(names)
    if interpreter()!=pin:raise Unsupported('trusted environment-check interpreter moved')
    profile={'schema_version':1,'profile':'isolated-stdlib-environment-checks-v1','environment':environment,
             'original_commands':[r['original_argv'] for r in requirements],'effective_commands':effective,'interpreter':pin,
             'setup_policy':'absent-or-empty-before-script; no external execution inputs',
             'local_environment_policy':'isolated-python-no-site; remote variables/lifecycle are not reproduced'}
    chain={'schema_version':1,'provider':'gitlab','workflow':None,'job':None,'applicability':None,'setup':[],
           'requirements':requirements,'source_chain':[],'complete':True,'environment_profile':profile}
    argv=effective[0] if len(effective)==1 else ['sh','-eu','-c','\n'.join(shlex.join(v) for v in effective)]
    return {'verification':checks,'verification_chain':chain,'source_dependencies':[],'input_scope':sorted(set(inputs)),
            'argv':argv,'cwd':'.','execution_kind':'local','env_names':['PATH']}



def go_environment_rules(*, make_context=False):
    rules = {"GOENV": "off", "GOFLAGS": "", "GOWORK": ""}
    if make_context:
        rules.update({name: "" for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES")})
    return rules


def go_environment(*, make_context=False):
    """Qualify only observed defaults; never open or rewrite user Go configuration."""
    from al_environment_observation import environment as fingerprints

    rules = go_environment_rules(make_context=make_context)
    inputs = fingerprints(rules)
    problems = []
    for name, expected in rules.items():
        observed = inputs[name]
        if not expected and not observed["present"]:
            continue
        if (
            not observed["present"]
            or observed["digest"] != hashlib.sha256(expected.encode()).hexdigest()
        ):
            problems.append(name + " does not meet the supported effective-environment contract")
    if problems:
        raise Unsupported("unsupported Go environment: " + "; ".join(problems))
    return {
        "schema_version": 1,
        "profile": "closed-go-environment-v1",
        "inputs": inputs,
        "configuration_files_read": False,
        "source_environment_rewritten": False,
    }


def go_environment_guard(*, make_context=False):
    """Check the actual launch environment; all names/values below are trusted constants."""
    checks = []
    for name, expected in go_environment_rules(make_context=make_context).items():
        value = '"${' + name + '-}"'
        checks.append("[ " + value + " = off ]" if expected else "[ -z " + value + " ]")
    return (
        "if ! { " + " && ".join(checks) + "; }; then\n"
        "  printf '%s\\n' 'AL Go verification environment is unqualified' >&2\n"
        "  exit 126\nfi"
    )
