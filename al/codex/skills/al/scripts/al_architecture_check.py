"""Bundled generic module-ban check, configured by tool.al.architecture.forbidden."""
import argparse
import ast
from pathlib import Path
import tomllib


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--unit',required=True);args=parser.parse_args()
    unit=Path(args.unit)
    if unit.is_absolute() or '..' in unit.parts:parser.error('unit must be repository relative')
    config=tomllib.loads((unit/'pyproject.toml').read_text());forbidden=config['tool']['al']['architecture']['forbidden']
    # Match the reviewed literal source-root forms used by discovery.
    tool=config.get('tool',{});st=tool.get('setuptools',{});packages=st.get('packages',{})
    roots=packages.get('find',{}).get('where') if isinstance(packages,dict) else None
    roots=roots or list(st.get('package-dir',{}).values()) or [tool.get('uv',{}).get('build-backend',{}).get('module-root','src' if (unit/'src').is_dir() else '.')]
    failed=[]
    for root in roots:
        for file in sorted((unit/root).rglob('*.py')):
            if any(p in {'.git','.venv','__pycache__','node_modules'} for p in file.parts):continue
            if file.is_symlink():raise SystemExit('unsafe symlink source '+str(file))
            try:tree=ast.parse(file.read_text())
            except (OSError,SyntaxError,UnicodeError) as exc:failed.append(str(exc));continue
            names=[]
            aliases={a.asname or a.name for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module in {'importlib','builtins'} for a in n.names if a.name in {'import_module','__import__'}}
            aliases.update(n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and n.id=='__import__')
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):names.extend(a.name for a in node.names)
                elif isinstance(node,ast.ImportFrom):names.append(node.module or '')
                elif isinstance(node,ast.Call) and ((isinstance(node.func,ast.Name) and node.func.id in aliases) or (isinstance(node.func,ast.Attribute) and node.func.attr=='import_module')):
                    if node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str):names.append(node.args[0].value)
                    else:failed.append(str(file)+': nonliteral dynamic import cannot satisfy module-ban policy')
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'exec','eval'}:failed.append(str(file)+': runtime code execution cannot satisfy module-ban policy')
            for name in names:
                if any(name==b or name.startswith(b+'.') for b in forbidden):failed.append(str(file)+': forbidden import '+name)
    for error in failed:print(error)
    raise SystemExit(1 if failed else 0)


if __name__=='__main__':main()
