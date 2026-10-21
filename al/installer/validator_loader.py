"""Captured-byte imports with truthful origins and no target import fallback."""
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import sysconfig


class ClosedLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, captured, platform, *, installers=False):
        self.captured, self.platform, self.installers = captured, platform, installers
        self.root = Path(captured['snapshot'])
        self.modules = dict(captured['origin']['platforms'][platform]['modules'])
        if installers:
            self.modules.update({'installer.'+Path(name).stem:name for name in captured['bytes']
                                 if name.startswith('installer/') and name.endswith('.py')})
            self.modules['installer'] = 'installer/__init__.py'
        self.loaded = {}
        self.stdlib = {Path(sysconfig.get_path(key)).resolve() for key in ('stdlib', 'platstdlib')}

    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('al_') or fullname == 'installer' or fullname.startswith('installer.'):
            if fullname not in self.modules:raise ImportError('unbound validator module: '+fullname)
            filename = str(self.root / self.modules[fullname])
            return importlib.util.spec_from_file_location(fullname, filename, loader=self,
                                                          submodule_search_locations=[str(self.root/'installer')] if fullname=='installer' else None)
        for finder in (importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter):
            spec = finder.find_spec(fullname, path)
            if spec is not None:return spec
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None:raise ModuleNotFoundError('unknown validator import: '+fullname,name=fullname)
        if not spec.origin or spec.origin in {'built-in','frozen'}:return spec
        if not isinstance(spec.loader,(importlib.machinery.SourceFileLoader,importlib.machinery.SourcelessFileLoader,importlib.machinery.ExtensionFileLoader)):
            raise ImportError('unbound validator import loader: '+fullname)
        origin = Path(spec.origin).resolve()
        if any(part in {'site-packages','dist-packages'} for part in origin.parts) or not any(origin.is_relative_to(root) for root in self.stdlib):
            raise ImportError('nonstdlib validator import refused: '+fullname)
        return spec

    def create_module(self, spec):return None

    def exec_module(self, module):
        name = module.__name__
        relative = self.modules[name]
        filename = str(self.root/relative)
        raw = self.captured['bytes'][relative]
        module.__file__ = filename
        self.loaded[name] = {'origin':filename, 'sha256':hashlib.sha256(raw).hexdigest()}
        exec(compile(raw, filename, 'exec'), module.__dict__)

    def get_filename(self, fullname):return str(self.root/self.modules[fullname])


def install_closed_loader(captured, platform, *, installers=False):
    if platform not in captured['origin']['platforms']:raise ValueError('unknown validator platform')
    finder = ClosedLoader(captured, platform, installers=installers)
    if any(name.startswith('al_') or installers and (name=='installer' or name.startswith('installer.')) for name in sys.modules):raise ValueError('foreign preloaded runtime module')
    sys.meta_path.insert(0, finder)
    return finder


def verify_loaded_modules(context):
    for name,module in list(sys.modules.items()):
        if not (name.startswith('al_') or context.installers and (name=='installer' or name.startswith('installer.'))):continue
        row = context.loaded.get(name)
        if (not row or module.__loader__ is not context or module.__file__ != row['origin']
                or module.__spec__.origin != row['origin']):
            raise ValueError('validator loaded module provenance changed: '+name)
    return dict(context.loaded)
