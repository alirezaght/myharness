"""Source-bound adapter for finite literal Hatchling wheel package selection."""
from pathlib import PurePosixPath
import re
from al_repository_facts import gap,safe,within


def packages(scan,facts,path,config):
    prefix='' if path=='.' else path+'/'
    source=prefix+'pyproject.toml'
    try:
        hatch=config['tool']['hatch']
        if not isinstance(hatch,dict) or set(hatch)!={'build'}:raise ValueError('only literal build.targets.wheel.packages is supported')
        build=hatch['build']
        if not isinstance(build,dict) or set(build)!={'targets'}:raise ValueError('build hooks and selection settings are unsupported')
        targets=build['targets']
        if not isinstance(targets,dict) or set(targets)!={'wheel'}:raise ValueError('only the literal wheel target is supported')
        wheel=targets['wheel']
        if not isinstance(wheel,dict) or set(wheel)!={'packages'}:raise ValueError('wheel remapping and selection settings are unsupported')
        declared=wheel['packages']
        if not isinstance(declared,list) or not 1<=len(declared)<=64:raise ValueError('packages must be a bounded nonempty literal list')
        if config.get('build-system',{}).get('backend-path'):raise ValueError('custom backend paths are unsupported')
        selected=[];roots=[];names=set()
        for value in declared:
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*',value):raise ValueError('package paths must be finite contained literal directories')
            package=PurePosixPath(prefix,value).as_posix();safe(package)
            if package+'/' not in scan.members or not any(within(package,p) and p.endswith('.py') for p in scan.files):raise ValueError('declared package directory is absent or has no captured Python source: '+value)
            name=PurePosixPath(package).name
            if name in names or any(within(p,package) or within(package,p) for p in selected):raise ValueError('overlapping or ambiguous package selection')
            names.add(name);selected.append(package);roots.append(str(PurePosixPath(package).parent))
        return sorted(set(roots)),sorted(selected)
    except (KeyError,TypeError,ValueError) as exc:
        facts['enumeration']['gaps'].append(gap('unsupported-hatch-layout',source,str(exc),path))
        return [],[]
