"""Bounded fixture-only conftest/helper declarations; unknown import effects refuse."""
import ast
import os
from pathlib import Path
from al_ci_yaml import Unsupported

STDLIB={'os','sys','pathlib','json','hashlib','subprocess','shutil','tempfile','sqlite3','time'}
def literal(value):
    try:return ast.literal_eval(value)
    except (ValueError,TypeError,SyntaxError):raise Unsupported('nonliteral pytest helper declaration') from None


def annotation(value):
    if value is not None and not isinstance(value,ast.Name):literal(value)


EXCLUDED={'.git','.venv','venv','__pycache__','.pytest_cache','node_modules','build','dist'}


def _local(root,module):
    path=root.joinpath(*module.split('.'))
    return next((p for p in (path.with_suffix('.py'),path/'__init__.py') if p.is_file()),None)


def fixture_sources(root,roots,read):
    conftests=set();directories=set();count=0
    for name in roots:
        path=root/name
        if path.is_file():path=path.parent
        current=path
        while current==root or root in current.parents:
            directories.add(current.relative_to(root).as_posix())
            if (current/'conftest.py').exists():conftests.add((current/'conftest.py').relative_to(root).as_posix())
            if current==root:break
            current=current.parent
        for parent,dirs,files in os.walk(path,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in EXCLUDED and not d.startswith('.'))
            if any((Path(parent)/d).is_symlink() for d in dirs):raise Unsupported('unsafe pytest collection directory')
            count+=len(dirs)+len(files)
            if count>10000:raise Unsupported('pytest collection observation limit')
            directories.add(Path(parent).relative_to(root).as_posix())
            if 'conftest.py' in files:conftests.add((Path(parent)/'conftest.py').relative_to(root).as_posix())
    pending=list(sorted(conftests));seen=set()
    while pending:
        name=pending.pop()
        if name in seen:continue
        seen.add(name)
        if len(seen)>128:raise Unsupported('pytest helper closure limit')
        tree=ast.parse(read(name),filename=name);pytest_aliases=set()
        for node in tree.body:
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):continue
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                modules=[v.name for v in node.names] if isinstance(node,ast.Import) else [node.module or '']
                if isinstance(node,ast.ImportFrom) and node.level:raise Unsupported('relative pytest helper imports need explicit classification')
                for module in modules:
                    if module=='pytest':
                        if isinstance(node,ast.ImportFrom):raise Unsupported('direct pytest helper import unsupported')
                        pytest_aliases.update(v.asname or v.name for v in node.names);continue
                    local=_local(root,module)
                    if local:
                        pending.append(local.relative_to(root).as_posix())
                        for parent in local.parents:
                            if parent==root:break
                            init=parent/'__init__.py'
                            if init.is_file():pending.append(init.relative_to(root).as_posix())
                    elif module.split('.')[0] not in STDLIB:raise Unsupported('unmodeled imported pytest helper '+module)
                continue
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                if node.name.startswith('pytest_'):raise Unsupported('unmodeled pytest hook '+node.name)
                annotations=[node.returns,*[a.annotation for a in [*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs,*([node.args.vararg] if node.args.vararg else []),*([node.args.kwarg] if node.args.kwarg else [])] if a.annotation]]
                for value in annotations:annotation(value)
                for default in [*node.args.defaults,*[v for v in node.args.kw_defaults if v is not None]]:literal(default)
                for decorator in node.decorator_list:
                    call=decorator.func if isinstance(decorator,ast.Call) else decorator
                    if not isinstance(call,ast.Attribute) or not isinstance(call.value,ast.Name) or call.value.id not in pytest_aliases or call.attr!='fixture':
                        raise Unsupported('unmodeled pytest helper decorator')
                    if isinstance(decorator,ast.Call):
                        if decorator.args or any(k.arg not in {'autouse','scope','name'} for k in decorator.keywords):raise Unsupported('unsupported fixture declaration')
                        for keyword in decorator.keywords:literal(keyword.value)
                # Function bodies execute as fixtures/helpers, not collection
                # configuration. Actual outcomes remain required by the runner.
                continue
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                if isinstance(node,ast.AnnAssign):annotation(node.annotation)
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                if any(not isinstance(t,ast.Name) or t.id in pytest_aliases|{'pytest_plugins','collect_ignore','collect_ignore_glob'} for t in targets):raise Unsupported('pytest configuration mutation')
                literal(node.value);continue
            raise Unsupported('unmodeled executable pytest helper declaration: '+name)
    return {'profile':'fixture-declarations-v1','files':sorted(conftests),'dependencies':sorted(seen),'directories':sorted(directories)}
