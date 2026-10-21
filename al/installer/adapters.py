"""Resource mappings and configuration desires for each platform."""
from pathlib import Path
from .targets import config_path, package_path, roles_path


def platform_resources(cache, manifest, target, platform):
    payload = Path(cache) / 'payload'
    package = payload / platform / 'skills/al'
    roles = {}
    files = {}
    for name, item in manifest['files'].items():
        if item['platform'] != platform:
            continue
        fingerprint = {key: item[key] for key in ('sha256', 'mode')}
        if item['kind'] == 'role':
            destination = roles_path(target, platform) / Path(name).name
            roles[str(destination)] = {'source': str(payload / name), 'state': fingerprint}
        else:
            files[name.split('/skills/al/', 1)[1]] = fingerprint
    desired = {}
    if platform == 'codex':
        desired['agents.max_depth'] = {'present': True, 'value': 1}
        for path in roles:
            name = Path(path).stem
            desired['agents.' + name + '.config_file'] = {'present': True, 'value': path}
    return {'package': str(package), 'target': str(package_path(target, platform)),
            'files': files, 'roles': roles, 'config': str(config_path(target, platform)),
            'desired_config': desired}
