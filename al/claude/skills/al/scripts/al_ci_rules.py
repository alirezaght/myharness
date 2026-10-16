"""Conservative CI rule evaluation over selected paths, without ambient context."""
import copy
import re
from al_ci_yaml import Reference,Unsupported
import al_ci_expression as E

GLOBAL={'include','workflow','stages','variables','default','image','services','before_script','after_script','cache','spec'}
RELEASE={'deploy','deployment','release','provision','migrate','migration','cleanup','destroy','apply'}


def merge(left,right):
    result=copy.deepcopy(left)
    for key,value in right.items():
        result[key]=merge(result[key],value) if isinstance(value,dict) and isinstance(result.get(key),dict) else copy.deepcopy(value)
    return result


def inherited(name,definitions,trail=(),used=None):
    if used is not None:used.add(name)
    if name in trail or len(trail)>32:raise Unsupported('cyclic CI inheritance/reference')
    item=definitions.get(name)
    if not isinstance(item,dict) or '__unsupported__' in item:raise Unsupported('missing/unsupported CI definition '+name)
    parents=item.get('extends',[]);parents=[parents] if isinstance(parents,str) else parents
    if not isinstance(parents,list):raise Unsupported('nonliteral extends')
    value={}
    for parent in parents:
        if not isinstance(parent,str):raise Unsupported('nonliteral extends')
        value=merge(value,inherited(parent,definitions,trail+(name,),used))
    value=merge(value,{k:v for k,v in item.items() if k!='extends'})
    return value


def references(value,definitions,trail=(),used=None):
    if len(trail)>32:raise Unsupported('cyclic CI reference')
    if isinstance(value,Reference):
        if not value or not all(isinstance(v,str) for v in value):raise Unsupported('invalid CI reference')
        key=tuple(value)
        if key in trail:raise Unsupported('cyclic CI reference')
        result=inherited(value[0],definitions,used=used)
        for name in value[1:]:
            if not isinstance(result,dict) or name not in result:raise Unsupported('missing CI reference field')
            result=result[name]
        return references(result,definitions,trail+(key,),used)
    if isinstance(value,list):
        result=[]
        for child in value:
            resolved=references(child,definitions,trail,used)
            result.extend(resolved if isinstance(resolved,list) else [resolved])
        return result
    if isinstance(value,dict):return {k:references(v,definitions,trail,used) for k,v in value.items()}
    return value


def unit(path):return '/'.join(path.split('/')[:2]) if path.startswith(('apps/','services/','packages/')) else '.'


def pattern_matches(pattern,path,directory=False):
    if not isinstance(pattern,str) or any(c in pattern for c in '${}[]\\'):return None
    if directory:
        if path=='.':return None
        prefix=re.split(r'[*?]',pattern,maxsplit=1)[0];candidate=path.rstrip('/')+'/'
        return None if not prefix or prefix.startswith(candidate) or candidate.startswith(prefix) else False
    regex='';i=0
    while i<len(pattern):
        if pattern.startswith('**/',i):regex+='(?:.*/)?';i+=3
        elif pattern.startswith('**',i):regex+='.*';i+=2
        elif pattern[i]=='*':regex+='[^/]*';i+=1
        elif pattern[i]=='?':regex+='[^/]';i+=1
        else:regex+=re.escape(pattern[i]);i+=1
    return bool(re.fullmatch(regex,path))


def changes(value,paths,directories=()):
    if isinstance(value,dict):
        if set(value)-{'paths'}:return None,set(paths)
        value=value.get('paths')
    if not isinstance(value,list) or not value:return None,set(paths)
    matching=set();unknown=False
    for pattern in value:
        for path in paths:
            result=pattern_matches(pattern,path,path in directories)
            if result is None:unknown=True
            elif result:matching.add(path)
    return (None,set(paths)) if unknown else (True,matching) if matching else (False,set())


def rules(value,paths,context=None,directories=()):
    if not isinstance(value,list):return None,set(paths),'unsupported CI rules'
    resolved=[]
    for rule in value:
        if not isinstance(rule,dict) or set(rule)-{'if','changes','when','allow_failure','variables','needs','interruptible','auto_cancel'}:return None,set(paths),'unsupported CI rule expression'
        if 'auto_cancel' in rule and not E.scheduling(rule['auto_cancel']):return None,set(paths),'unsupported scheduling metadata'
        if 'when' in rule and (not isinstance(rule['when'],str) or rule['when'] not in {'always','never','on_success','on_failure','manual','delayed'}):return None,set(paths),'unsupported rule scheduling condition'
        if 'changes' in rule:
            change=rule['changes']
            if isinstance(change,dict):
                if set(change)-{'paths','compare_to'} or 'compare_to' in change and not isinstance(change['compare_to'],str):return None,set(paths),'unsupported CI changes structure'
                change=change.get('paths')
            if not isinstance(change,list) or not change or any(not isinstance(p,str) or not p for p in change):return None,set(paths),'malformed CI changes paths'
        try:condition=E.evaluate(rule.get('if',True),context,strict=True)
        except ValueError:return None,set(paths),'unsupported CI condition expression'
        resolved.append((rule,condition))
    uncertainty=False;possible=set()
    for rule,condition in resolved:
        if condition is False:continue
        unknown=condition is None
        match,inputs=changes(rule['changes'],paths,directories) if 'changes' in rule else (True,set(paths))
        if match is False:continue
        if match is None or unknown:
            uncertainty=True;possible|=inputs;continue
        if uncertainty:return None,possible|inputs,'pipeline context/rule applicability unresolved'
        if rule.get('when')=='never':return False,set(),'explicitly excluded by sourced rules'
        return True,inputs,'literal sourced rules select these inputs'
    return (None,possible or set(paths),'pipeline context/rule applicability unresolved') if uncertainty else (False,set(),'no sourced rule selects inputs')


def structure(job):
    stage=job.get('stage','test')
    if not isinstance(stage,str) or not stage.strip():raise Unsupported('malformed CI stage')
    environment=None
    if 'environment' in job:
        raw=job['environment']
        if isinstance(raw,str):name=raw;action='start'
        elif isinstance(raw,dict) and not set(raw)-{'name','action'}:name=raw.get('name');action=raw.get('action','start')
        else:raise Unsupported('malformed/unsupported CI environment')
        if not isinstance(name,str) or not name.strip() or not isinstance(action,str) or action not in {'start','stop','verify','prepare','access'}:raise Unsupported('malformed/unsupported CI environment fields')
        environment=(name,action)
    return stage,environment


def release(name,semantics):
    stage,environment=semantics
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',stage):return None
    stage=stage.lower()
    if stage in RELEASE:return True
    if environment is not None:
        name_value,action=environment
        if '$' in name_value:return None
        if action in {'start','stop'}:return True
        if action=='verify':return False
        return None
    if stage in {'test','verify','validate','lint','quality','coverage','security','acceptance'}:return False
    return None if any(p.lower() in RELEASE for p in name.split(':')) else False
