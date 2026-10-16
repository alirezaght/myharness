"""Explicit observational Git grammar, configuration and execution controls."""
import re

class GitError(ValueError):pass

GLOBAL_VALUES={'-C','--git-dir','--work-tree','--namespace'}
GLOBAL_FLAGS={'--no-pager','-P','--bare','--literal-pathspecs','--glob-pathspecs',
              '--noglob-pathspecs','--icase-pathspecs','--no-optional-locks','--no-replace-objects'}



def config(value):
    key,sep,setting=value.partition('=');key=key.lower()
    if not sep or not re.fullmatch(r'[a-z][a-z0-9.-]*',key):raise GitError('unsupported Git configuration override')
    if key in {'user.name','user.email'} and setting and not any(c in setting for c in '\r\n\0'):return
    bools={'true','false','yes','no','on','off','1','0'}
    if key in {'core.quotepath','diff.mnemonicprefix'} and setting.lower() in bools:return
    if key in {'color.ui','color.diff','color.status','color.branch'} and setting.lower() in bools|{'always','never','auto'}:return
    if key=='log.decorate' and setting.lower() in {'short','full','auto','no','false'}:return
    if key=='core.abbrev' and setting.isdigit() and 4<=int(setting)<=40:return
    raise GitError('executable or unknown Git configuration override rejected: '+key)


def parse(args):
    i=1
    while i<len(args) and args[i].startswith('-'):
        token=args[i];i+=1
        if token=='-c':
            if i>=len(args):raise GitError('Git -c operand missing')
            config(args[i]);i+=1;continue
        if token.startswith('-c') and not token.startswith('--'):config(token[2:]);continue
        if token in GLOBAL_VALUES:
            if i>=len(args) or not args[i]:raise GitError('Git global operand missing')
            i+=1;continue
        if any(token.startswith(option+'=') and token.split('=',1)[1] for option in GLOBAL_VALUES if option.startswith('--')):continue
        if token in GLOBAL_FLAGS:continue
        raise GitError('unsupported Git global option '+token)
    return (args[i],args[i+1:]) if i<len(args) else ('',[])


def cat_file(tail):
    flags={'-t','-s','-e','-p','-z','-Z','--buffer','--follow-symlinks','--unordered',
           '--batch-all-objects','--allow-unknown-type','--use-mailmap','--no-use-mailmap','--mailmap'}
    batches={'--batch','--batch-check','--batch-command'}
    i=0
    while i<len(tail):
        token=tail[i];i+=1
        if token=='--':return
        name=token.split('=',1)[0]
        if name in {'--filters','--textconv'}:raise GitError('Git cat-file filter execution')
        if token=='--path':
            if i>=len(tail):raise GitError('Git cat-file path operand missing')
            i+=1;continue
        if token.startswith('--path=') or name in batches or token in flags:continue
        if token.startswith('-'):raise GitError('unsupported Git cat-file option '+token)


def inspect(args,environment):
    if any(name.startswith('GIT_') or name in {'PAGER','SSH_ASKPASS'} for name in environment):raise GitError('invocation-supplied Git execution/config environment override')
    sub,tail=parse(args)
    if sub=='config':
        from al_git_config import inspect as query
        query(tail,GitError);return sub,tail
    if sub=='cat-file':cat_file(tail);return sub,tail
    from al_git_options import inspect as options
    options(sub,tail,GitError)
    return sub,tail
