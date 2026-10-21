"""Reviewed literal verification tools and their repository-relative targets."""
from pathlib import PurePosixPath
from al_repository_facts import within


def observed(tokens,pytest_profile=None):
    args=list(tokens)
    if not args or '/' in args[0]:return []
    if args[:2]==['uv','build']:
        return [{'tool':'uv-build','classes':['python'],'paths':['.']}] if len(args)==2 else []
    if args[:2]==['uv','run']:
        args=args[2:]
        while args and args[0] in {'--frozen','--locked','--no-sync'}:args=args[1:]
        if not args or '/' in args[0]:return []
    if args and PurePosixPath(args[0]).name in {'python','python3'}:
        if len(args)<3 or args[1]!='-m':return []
        tool=args[2];rest=args[3:]
    elif args:
        tool=PurePosixPath(args[0]).name;rest=args[1:]
    else:return []
    if any(a in {'--exit-zero','--help','-h','--version','--collect-only','--co'} for a in rest):return []
    if tool=='json.tool':
        if len(rest)!=1 or rest[0].startswith('-'):return []
        classes=['manifest'];targets=rest
    elif tool=='compileall':
        if any(a.startswith('-') and a not in {'-q','-qq'} for a in rest):return []
        classes=['python'];targets=[a for a in rest if not a.startswith('-')] or ['.']
    elif tool in {'pytest','mypy'}:
        # Only reviewed non-masking flags. Unsupported options keep the CI job
        # sourced, but cannot establish service-verification completeness.
        allowed={'-q','-qq','-x','--strict-markers','--strict-config','--disable-warnings','--no-incremental'}
        if tool=='pytest' and pytest_profile is not None:
            from al_pytest_profile import OPTIONS
            allowed=OPTIONS
        if any(a.startswith('-') and a not in allowed for a in rest):return []
        classes=['python'];targets=[a.split('::',1)[0] for a in rest if not a.startswith('-')] or ['.']
    elif tool=='ruff' and rest[:1]==['check']:
        if any(a.startswith('-') and a not in {'-q','--quiet','--no-cache'} for a in rest[1:]):return []
        classes=['python'];targets=[a for a in rest[1:] if not a.startswith('-')] or ['.']
    elif tool=='ruff' and rest[:2]==['format','--check']:
        if any(a.startswith('-') and a not in {'--quiet','--no-cache'} for a in rest[2:]):return []
        classes=['python'];targets=[a for a in rest[2:] if not a.startswith('-')] or ['.']
    elif tool=='node' and rest[:1]==['--check'] and len(rest)==2:
        classes=['javascript'];targets=rest[1:]
    elif tool=='go' and rest[:1] in (['test'],['vet']):
        if any(a.startswith('-') and a not in {'-race','-v'} for a in rest[1:]):return []
        classes=['go'];targets=[a.removesuffix('/...') for a in rest[1:] if not a.startswith('-')] or ['.']
    else:return []
    normalized=[]
    for target in targets:
        p=PurePosixPath(target)
        if p.is_absolute() or '..' in p.parts or any(c in target for c in '*?[]$'):return []
        normalized.append(p.as_posix())
    record={'tool':tool,'classes':classes,'paths':sorted(set(normalized))}
    if tool=='pytest' and pytest_profile is not None:
        record.update(paths=list(pytest_profile['collection_roots']),collection_roots=list(pytest_profile['collection_roots']),application_scope=pytest_profile['application_scope'])
    return [record]


def covers(unit,records):
    for record in records:
        if record['classes']==['manifest']:
            prefix='' if unit['path']=='.' else unit['path']+'/'
            if prefix+'package.json' in record['paths'] and 'javascript' in unit['languages']:return True
        elif set(record['classes'])&set(unit['languages']):
            for path in record['paths']:
                if within(path,unit['path']):return True
                if within(unit['path'],path) and (unit['path']!='.' or any(within(root,path) or within(path,root) for root in unit['source_roots'])):return True
    return False


def purpose(tokens, records):
    if not records:return 'other'
    tool=records[0]['tool']
    if tool=='uv-build':return 'build'
    if tool=='ruff':return 'format-check' if 'format' in tokens else 'lint'
    if tool in {'pytest','go'}:return 'test'
    return 'verification'


def conventional_verification(unit, obligations, bindings, enumeration):
    relevant=[o for o in obligations if unit['path'] in o['units'] or '.' in o['units']]
    result={'satisfied':False,'obligation_ids':[],'binding_ids':[],'proof':[],'gaps':[]}
    if enumeration.get('gaps') or not relevant:return result
    records=[]
    for obligation in relevant:
        execution=obligation.get('execution')
        chain=execution.get('verification_chain') if execution else None
        matches=[b for b in bindings if b['category']=='ci' and b['source_chain'] and b['source_chain'][0]['path']==obligation['source_path'] and b['source_chain'][0]['selector']==obligation['job']]
        if obligation['applicability']!='APPLICABLE' or not chain or chain.get('complete') is not True or len(matches)!=1:
            result['gaps'].append(obligation['source_path']+':'+obligation['job']);continue
        result['binding_ids'].append(matches[0]['id']);result['obligation_ids'].extend(matches[0]['obligation_ids'])
        result['proof'].append(chain);records.extend(execution.get('verification',[]))
    for language in unit['languages']:
        candidates=[r for r in records if language in r['classes']]
        roots=unit['source_roots'] if language=='python' else [unit['path']]
        for root in roots:
            if not any(within(path,root) for record in candidates for path in record['paths']):
                manifest=language=='javascript' and any(r['classes']==['manifest'] and covers(unit,[r]) for r in records)
                suite=language=='python' and any(r['tool']=='pytest' and r.get('application_scope',{}).get('kind')=='repository-mandated-ci-suite' and any(within(p,root) for p in r['application_scope']['roots']) for r in records)
                if not manifest and not suite:result['gaps'].append('uncovered '+language+' source boundary: '+root)
    suite=any(r['tool']=='pytest' and r.get('application_scope',{}).get('kind')=='repository-mandated-ci-suite' and any(within(p,unit['path']) for p in r['application_scope']['roots']) for r in records)
    result['satisfied']=not result['gaps'] and (covers(unit,records) or suite)
    return result


def pytest_arguments(tokens):
    args=list(tokens)
    if args[:2]==['uv','run']:
        args=args[2:]
        while args and args[0] in {'--frozen','--locked','--no-sync'}:args=args[1:]
    if args[:1] in (['python'],['python3']):args=args[2:] if args[1:2]==['-m'] else []
    return args[1:] if args[:1]==['pytest'] else None
