"""Locked setup in an owned disposable checkout before repository hooks."""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
import subprocess
import time
import tomllib
import shlex

from al_checkout_paths import MaterializationError, validate
from al_gate_process import stop
from al_verification_inputs import executable
from al_package_output import InspectionError,inspect_call


class SetupError(MaterializationError):
    pass


def plan(root, cwd='.', *, package_root=None):
    root = Path(root).resolve()
    current = root / cwd
    if root != current and root not in current.parents:
        raise SetupError('setup working directory escapes checkout')
    # A workspace lock at an ancestor governs its member packages.
    ancestors = list(reversed([current, *current.parents[:len(current.parts)-len(root.parts)]]))
    if package_root is not None:
        selected=Path(package_root)
        if selected.is_absolute() or '..' in selected.parts or selected.as_posix()!=package_root:
            raise SetupError('package setup root must be canonical and repository-relative')
        directory=root/selected
        if directory not in ancestors or directory.is_symlink():raise SetupError('package setup root is outside the member ancestry')
        ancestors=[directory]
    for directory in ancestors:
        if directory != root and root not in directory.parents:
            continue
        manager = None
        if (directory/'pnpm-lock.yaml').is_file():
            manager, argv, names = 'pnpm', ['pnpm', 'install', '--frozen-lockfile'], ['package.json', 'pnpm-lock.yaml']
            if (directory/'pnpm-workspace.yaml').is_file(): names.append('pnpm-workspace.yaml')
        elif (directory/'package-lock.json').is_file():
            manager, argv, names = 'npm', ['npm', 'ci'], ['package.json', 'package-lock.json']
        elif (directory/'uv.lock').is_file():
            manager, argv, names = 'uv', ['uv', 'sync', '--locked'], ['pyproject.toml', 'uv.lock']
        if manager:
            inputs = []
            for name in names:
                path = directory/name
                if path.is_symlink() or not path.is_file():
                    raise SetupError('setup requires a regular locked input: '+name)
                inputs.append({'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            if manager in {'npm', 'pnpm'}:
                package = json.loads((directory/'package.json').read_text())
                declared = package.get('packageManager')
                if declared and not declared.startswith(manager+'@'):
                    raise SetupError('package manager contradicts the selected lockfile')
            return {'schema_version': 1, 'kind': 'check-setup-v1', 'manager': manager, 'argv': argv,
                    'cwd': directory.relative_to(root).as_posix(), 'inputs': inputs}
    if any((directory/name).is_file() for directory in ancestors for name in ('package.json','pyproject.toml')):
        raise SetupError('package-backed hook has no supported lockfile; supply locked setup')
    return None


def _tracked(root):
    names = subprocess.check_output(['git','ls-files','-z'], cwd=root).split(b'\0')
    result = {}
    for raw in names:
        if not raw:continue
        name = os.fsdecode(raw); path = root/name
        if path.is_symlink():value = ('link',os.readlink(path))
        elif path.is_file():value = ('file',path.stat().st_mode & 0o111,hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():value = ('dir',)
        else:value = ('absent',)
        result[name] = value
    return result


def needs_setup(root, argv):
    """A source-free/stdlib-only hook needs no package install.

    A lock or declared package dependency is positive setup evidence. Missing
    lock-backed tooling stays a prerequisite; it is never borrowed globally.
    """
    root=Path(root)
    if any((root/name).is_file() for name in ['uv.lock','package-lock.json','pnpm-lock.yaml']):return True
    pyproject=root/'pyproject.toml'
    if pyproject.is_file():
        try:
            data=tomllib.loads(pyproject.read_text())
            project=data.get('project',{})
            if project.get('dependencies') or any(project.get('optional-dependencies',{}).values()) or any(data.get('dependency-groups',{}).values()):return True
        except (ValueError,OSError):return True
    package=root/'package.json'
    if package.is_file():
        try:
            data=json.loads(package.read_text())
            if any(data.get(key) for key in ['dependencies','devDependencies','optionalDependencies','packageManager']):return True
        except (ValueError,OSError):return True
    text=shlex.join(argv)
    if len(argv)>1 and argv[0] in {'sh','bash'} and not argv[1].startswith('-'):
        path=root/argv[1]
        if path.is_file():text=path.read_text()
    try:tokens=shlex.split(text,comments=True)
    except ValueError:return True
    return bool(set(tokens)&{'uv','npm','pnpm','npx','yarn','pytest','ruff','eslint','turbo','pre-commit'})


def execute(root, cwd, owner, log, timeout, records, argv=None, *, package_root=None, private_output=None):
    root = Path(root)
    if argv is not None and not needs_setup(root,argv):return
    try:recipe = plan(root,cwd) if package_root is None else plan(root,cwd,package_root=package_root)
    except (OSError,ValueError) as exc:raise SetupError('invalid locked setup: '+str(exc)) from exc
    if recipe is None:return
    row = dict(recipe, stage='setup', status='RUNNING', started_at=time.time(), hook='NOT_RUN')
    records.append(row)
    try:
        row['executable'] = executable(recipe['manager'])
        if recipe['manager'] in {'npm','pnpm'}:
            declared = json.loads((root/recipe['cwd']/'package.json').read_text()).get('packageManager')
            if declared:
                requested = declared.split('@',1)[1]
                version = owner.spawn([row['executable']['path'],'--version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT if private_output else subprocess.PIPE)
                try:
                    if private_output:
                        _,stdout,metadata=inspect_call(owner,Path(log),'setup-version',private_output.run,version,10)
                        if not metadata['complete']:raise SetupError('package manager version output/time budget exceeded')
                        from al_package_output import write_metadata
                        write_metadata(Path(log),'SETUP-VERSION',metadata)
                    else:stdout,_=version.communicate(timeout=10)
                finally:stop(version)
                if version.returncode or stdout.decode().strip()!=requested:
                    raise SetupError('installed package manager does not match packageManager '+declared)
        from al_gate_reuse import _tree
        before_git = _tree(root/'.git',True)
        before = _tracked(root)
        from al_gate_process import gate_environment
        env, _ = gate_environment()
        cache = root.parent/'dependency-cache'
        cache.mkdir(exist_ok=True)
        env.update(UV_PYTHON=str(Path(sys.executable).resolve()),UV_PYTHON_DOWNLOADS='never',UV_PROJECT_ENVIRONMENT=str(root/recipe['cwd']/'.venv'),UV_CACHE_DIR=str(cache/'uv'),
                   npm_config_cache=str(cache/'npm'),npm_config_update_notifier='false',CI='true')
        # Never inherit a working-tree dependency path into the disposable hook.
        env.pop('NODE_PATH',None)
        argv = [row['executable']['path'],*recipe['argv'][1:]]
        if private_output:
            from al_package_output import write_metadata
            write_metadata(Path(log),'SETUP',{'schema_version':1,'manager':recipe['manager'],'cwd':recipe['cwd'],'inputs':recipe['inputs']})
            proc=owner.spawn(argv,cwd=root/recipe['cwd'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            row['pid']=proc.pid
            code,raw,metadata=inspect_call(owner,Path(log),'setup-output',private_output.run,proc,timeout)
            del raw
            row['output']=metadata;row['cleanup']=owner.completion();row['exit']=code
            write_metadata(Path(log),'SETUP-OUTPUT',metadata)
            if not metadata['complete']:raise SetupError('locked dependency setup output/time budget exceeded')
            if code:raise SetupError('locked dependency setup failed with exit '+str(code))
        else:
            with Path(log).open('ab') as out:
                out.write(('setup: '+json.dumps(recipe,sort_keys=True)+'\n').encode());out.flush()
                proc = owner.spawn(argv,cwd=root/recipe['cwd'],env=env,stdout=out,stderr=subprocess.STDOUT)
                row['pid'] = proc.pid
                try:code = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    stop(proc);raise SetupError('locked dependency setup timed out')
                finally:
                    stop(proc)
                    row['cleanup'] = owner.completion()
                row['exit'] = code
                if code:raise SetupError('locked dependency setup failed with exit '+str(code))
        if recipe['manager']=='uv':
            interpreter=root/recipe['cwd']/'.venv/bin/python'
            if interpreter.is_symlink():
                target=interpreter.resolve(strict=True)
                if target!=Path(sys.executable).resolve():
                    raise SetupError('locked setup selected another Python interpreter; qualify it explicitly')
                row['python_interpreter']=executable(str(target))
                # Keep the ordinary checkout path guard strict: replace only
                # this owned venv link to the independently bound interpreter.
                copied=interpreter.with_name('python.al-copy')
                shutil.copy2(target,copied)
                copied.replace(interpreter)
        validate(root)
        if _tree(root/'.git',True)!=before_git:
            raise SetupError('locked dependency setup changed Git metadata')
        if _tracked(root)!=before:raise SetupError('locked dependency setup changed repository inputs')
        if not row['cleanup']['complete']:raise SetupError('locked dependency setup cleanup is unresolved')
        row['status'] = 'PASS'
    except InspectionError as exc:
        row['status']='INFRA';row['reason']=exc.metadata['reason'];row['cleanup']=exc.metadata['cleanup']
        raise
    except (OSError,ValueError,SetupError) as exc:
        row['status'] = 'INFRA';row['reason'] = str(exc)
        raise SetupError(str(exc)) from exc
    finally:row['ended_at'] = time.time()


def current_gaps(records):
    for row in records:
        if row.get('status')!='PASS' or not row.get('cleanup',{}).get('complete'):
            return ['dependency setup did not finish successfully']
        try:
            if executable(row['manager'])!=row['executable']:
                return ['dependency setup executable changed']
        except (OSError,ValueError):return ['dependency setup executable unavailable']
    return []
