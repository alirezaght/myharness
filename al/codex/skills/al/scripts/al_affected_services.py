"""Conservative Python consumers and the supported repository selection contract."""
import ast
import hashlib
import al_sources as Sources
from pathlib import Path

SELECTION='ci/scripts/import_review/checker.py'
SELECTION_AST='019c1a084a318b0a844add61bfe77cf21aa564a4896c6b1219e40fec1ca7d914'


def select(root,paths,opaque=()):
    def hidden(path):return any(Sources.contains(child,path) for child in opaque)
    paths=[path for path in paths if not hidden(path)]
    selected={str(Path(*Path(p).parts[:2])) for p in paths if p.startswith('services/')}
    shared=any(p.startswith('packages/') for p in paths)
    dependencies=[]
    if not shared:return sorted(selected),False,dependencies
    modules={};imports={};services={};uncertain=any(p.startswith(('packages/','services/')) for p in opaque)
    for pattern in ('packages/*/src/**/*.py','services/*/src/**/*.py'):
        for file in root.glob(pattern):
            if hidden(file.relative_to(root).as_posix()):continue
            relative=file.relative_to(root);parts=relative.parts;offset=parts.index('src')+1
            module='.'.join(parts[offset:])[:-3].removesuffix('.__init__')
            dependencies.append(relative.as_posix());modules[module]=relative.as_posix()
            if parts[0]=='services':services[module]='/'.join(parts[:2])
            try:
                tree=ast.parse(file.read_text());names=set()
                for n in ast.walk(tree):
                    if isinstance(n,ast.Import):names.update(a.name for a in n.names)
                    elif isinstance(n,ast.ImportFrom):
                        base=n.module or ''
                        if n.level:base='.'.join(module.split('.')[:-n.level]+([base] if base else []))
                        names.add(base);names.update(base+'.'+a.name for a in n.names)
                if any(isinstance(n,ast.Call) and (isinstance(n.func,ast.Name) and n.func.id=='__import__' or isinstance(n.func,ast.Attribute) and n.func.attr=='import_module') for n in ast.walk(tree)):uncertain=True
                imports[module]=names
            except (SyntaxError,UnicodeError):imports[module]=set();uncertain=True
    affected={module for module,path in modules.items() if any(path==p or path.startswith(p.rstrip('/')+'/') for p in paths)}
    while True:
        consumers={module for module,names in imports.items() if any(a==n or a.startswith(n+'.') or n.startswith(a+'.') for a in affected for n in names if n)}
        if consumers<=affected:break
        affected|=consumers
    selected.update(services[m] for m in affected if m in services)
    complete=False;selector=root/SELECTION
    if not hidden(SELECTION) and selector.is_file():
        dependencies.extend(p.relative_to(root).as_posix() for p in (root/'ci/scripts/import_review').glob('*.py') if not hidden(p.relative_to(root).as_posix()))
        try:
            fn=next(n for n in ast.parse(selector.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='affected_services')
            complete=hashlib.sha256(ast.dump(fn,include_attributes=False).encode()).hexdigest()==SELECTION_AST
        except (SyntaxError,StopIteration,UnicodeError):pass
    for file in root.glob('services/*/pyproject.toml'):
        if hidden(file.relative_to(root).as_posix()):continue
        dependencies.append(file.relative_to(root).as_posix())
        if complete and 'importlinter' in file.read_text():selected.add(file.parent.relative_to(root).as_posix())
    for name in ('ci/manifest.yml','ci/import-review-baseline.json'):
        if not hidden(name) and (root/name).is_file():dependencies.append(name)
    return sorted(selected),not complete or uncertain,sorted(set(dependencies))


def implementation_input(path):
    return path.startswith(('packages/','services/')) and '/src/' in path and path.endswith('.py')
