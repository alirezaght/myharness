"""Closed package-check interpretation. Execution authority never comes from a profile."""
from pathlib import PurePosixPath
import re


MASKING={'--passWithNoTests','--pass-with-no-tests','--listTests','--list-tests','--collect-only','--help','-h','--version','--fix','--write','-u','--updateSnapshot','--update-snapshots'}
TOOLS={'tsc':'typescript','eslint':'eslint','vitest':'vitest','jest':'jest'}
CONFIGS={'tsc':('tsconfig.json',),'eslint':('eslint.config.js','eslint.config.mjs','eslint.config.cjs','.eslintrc.json'),
         'vitest':('vitest.config.ts','vitest.config.js','vite.config.ts','vite.config.js'),
         'jest':('jest.config.js','jest.config.cjs','jest.config.mjs','jest.config.json')}


def _safe_target(unit,target):
    p=PurePosixPath(target)
    if p.is_absolute() or '..' in p.parts or any(c in target for c in '$`\\;|<>'):return None
    return (PurePosixPath(unit)/p).as_posix()


def classify(commands,unit,versions,config_text):
    records=[];reasons=[];kinds=set()
    for args in commands:
        exe=args[0];rest=args[1:];profile=None;kind=None;targets=[];config=[]
        start_reasons=len(reasons)
        names={a.split('=',1)[0] for a in rest}
        if names&MASKING:reasons.append('masking, mutating or observational-only flags');continue
        if any(a.split('=',1)[0] in {'--watch','--watchAll','-w'} and not a.endswith('=false') for a in rest):
            reasons.append('watch/interactive invocation');continue
        if exe=='node' and rest[:1]==['--check'] and len(rest)==2:
            profile='node-syntax';kind='check';targets=rest[1:]
        elif exe=='node' and rest[:1]==['--test']:
            allowed={'--test','--test-reporter=tap','--test-only','--test-concurrency=1'}
            if any(a.startswith('-') and a not in allowed for a in rest):reasons.append('unsupported Node test flags');continue
            profile='node-test';kind='test';targets=[a for a in rest[1:] if not a.startswith('-')] or ['.']
        elif exe=='tsc' and '--noEmit' in rest:
            options={'--noEmit','--pretty','--strict','--skipLibCheck'};values={'--project','-p'};targets=['.'];i=0
            while i<len(rest):
                a=rest[i];i+=1
                if a in values:
                    if i>=len(rest):reasons.append('missing TypeScript project');break
                    config.append(rest[i]);i+=1
                elif a in options:continue
                elif a=='false' and i>1 and rest[i-2]=='--pretty':continue
                else:reasons.append('unsupported TypeScript flags');break
            else:profile='typescript';kind='typecheck'
        elif exe=='eslint':
            options={'--no-cache','--quiet','--no-warn-ignored'};values={'--config','-c','--max-warnings','--ext'};i=0
            while i<len(rest):
                a=rest[i];i+=1
                if a in values:
                    if i>=len(rest):reasons.append('missing ESLint option operand');break
                    value=rest[i];i+=1
                    if a in {'--config','-c'}:config.append(value)
                    if a=='--max-warnings' and value!='0':reasons.append('unsupported ESLint warning allowance');break
                elif a in options:continue
                elif a.startswith('-'):reasons.append('unsupported ESLint flags');break
                else:targets.append(a)
            else:
                if targets:profile='eslint';kind='lint'
                else:reasons.append('ESLint source targets are not explicit')
        elif exe=='vitest' and (rest[:1]==['run'] or '--run' in rest):
            options={'run','--run','--coverage','--no-file-parallelism','--reporter=default','--reporter=verbose','--watch=false'}
            values={'--config','-c','--project','--maxWorkers','--minWorkers'};i=0
            while i<len(rest):
                a=rest[i];i+=1
                if a in values:
                    if i>=len(rest):reasons.append('missing Vitest option operand');break
                    if a in {'--config','-c'}:config.append(rest[i])
                    i+=1
                elif a in options:continue
                elif a.startswith('-'):reasons.append('unsupported Vitest flags');break
                else:targets.append(a)
            else:profile='vitest';kind='test';targets=targets or ['.']
        elif exe=='jest':
            options={'--ci','--runInBand','--coverage','--verbose','--watch=false','--watchAll=false','--no-cache'};values={'--config','-c','--testPathPattern','--testPathPatterns','--maxWorkers'};i=0
            while i<len(rest):
                a=rest[i];i+=1
                if a in values:
                    if i>=len(rest):reasons.append('missing Jest option operand');break
                    if a in {'--config','-c'}:config.append(rest[i])
                    i+=1
                elif a in options:continue
                elif a.startswith('-'):reasons.append('unsupported Jest flags');break
                else:targets.append(a)
            else:profile='jest';kind='test';targets=targets or ['.']
        if profile is None:
            if not reasons:reasons.append('framework or invocation semantics are not qualified')
            continue
        package=TOOLS.get(exe)
        from al_package_contracts import VERSIONS
        if package and versions.get(package) not in VERSIONS.get(package,set()):reasons.append('tool version has no qualified parser contract: '+package);continue
        paths=[_safe_target(unit,p) for p in targets]
        if any(p is None for p in paths):reasons.append('unsupported source target closure');continue
        configs=config or [name for name in CONFIGS.get(exe,()) if config_text(name) is not None]
        for name in configs:
            if _safe_target(unit,name) is None:reasons.append('configuration escapes member boundary');continue
            text=config_text(name)
            if text is None:reasons.append('configuration input unavailable');continue
            try:
                from al_package_contracts import config as configuration
                configuration(exe,text)
            except ValueError:reasons.append('configuration lacks a positive finite qualified closure')
        if exe=='tsc' and not configs:reasons.append('TypeScript project configuration missing')
        if len(reasons)!=start_reasons:continue
        kinds.add(kind);records.append({'profile':profile,'kind':kind,'targets':paths,'tool':package or 'node','version':versions.get(package) if package else None,'config':configs})
    if len(records)!=len(commands):reasons.append('not every authored command has qualified semantic coverage')
    return {'status':'UNVERIFIED' if reasons or not records else 'SUPPORTED','records':records,'kind':'test' if 'test' in kinds else next(iter(kinds)) if len(kinds)==1 else 'check','reasons':sorted(set(reasons))}


def result(profile,code,text):
    """Raw successful execution is distinct from a semantically usable result."""
    import al_ledger as L
    import al_runner_output
    semantic=profile['semantic_coverage'];text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text);counts=L.parse_counts(text);reasons=[]
    if any(r['profile']=='node-test' for r in semantic['records']):
        passed=re.findall(r'^(?:#|ℹ)\s+pass\s+(\d+)\s*$',text,re.M);failed=re.findall(r'^(?:#|ℹ)\s+fail\s+(\d+)\s*$',text,re.M)
        if passed:counts['passed']=int(passed[-1])
        if failed:counts['failed']=max(counts.get('failed',0),max(map(int,failed)))
    execution='PASS' if code==0 else 'INFRA' if code in {126,127} else 'FAIL'
    problem=al_runner_output.output_problem(text)
    if execution!='PASS':verdict=execution
    elif problem:verdict='INFRA';reasons.append(problem)
    elif counts.get('failed') or counts.get('error'):verdict='FAIL';reasons.append('runner reported failures despite exit zero')
    elif L.NOTHING_RAN_RE.search(text):verdict='EMPTY';reasons.append('runner explicitly reported no tests')
    elif semantic['kind']=='test' or profile['intent']=='test':
        if counts.get('passed')==0 or L.NOTHING_RAN_RE.search(text):verdict='EMPTY';reasons.append('no tests demonstrated')
        elif semantic['status']!='SUPPORTED' or not counts.get('passed'):verdict='UNVERIFIED';reasons.append('test semantics/count are not verified')
        else:verdict='VERIFIED'
    elif semantic['status']=='SUPPORTED':verdict='VERIFIED'
    else:verdict='UNVERIFIED';reasons.append('execution succeeded; semantic coverage remains unverified')
    return {'schema_version':1,'profile_sha256':__import__('al_repository_facts').digest(profile),'execution':execution,'exit':code,'semantic_verdict':verdict,'counts':counts,'reasons':reasons}
