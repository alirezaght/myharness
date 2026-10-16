"""Explicit option grammars for executable wrapper chains."""
import re
import shutil
from pathlib import PurePosixPath

class WrapperError(ValueError):
    pass


def assignment(word):return bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*',word))


def argument(args,option):
    if not args:raise WrapperError('missing wrapper operand for '+option)
    return args.pop(0)


class Resolved(list):
    def __init__(self,args,environment):
        super().__init__(args);self.environment=environment


def unwrap(values,find_context=False):
    args=list(values);environment=[]
    def assignments():
        while args and assignment(args[0]):environment.append(args.pop(0).split('=',1)[0])
    assignments()
    if not args:return Resolved([],environment)
    while args:
        if find_context and '{}' in args[0]:raise WrapperError('find replacement at executable position')
        if args[0]=='--':raise WrapperError('separator at executable position')
        if any(x in args[0] for x in '$*?[') or args[0] in {'{','}'}:raise WrapperError('unknown executable expansion')
        if any(c.isspace() for c in args[0]) and shutil.which(args[0]) is None:raise WrapperError('unknown quoted executable')
        args[0]=PurePosixPath(args[0]).name
        wrapper=args[0]
        if wrapper not in {'env','command','sudo','timeout','nice'}:return Resolved(args,environment)
        args.pop(0)
        if wrapper=='sudo':raise WrapperError('privileged wrapper unsupported')
        while args and args[0].startswith('-'):
            option=args.pop(0)
            if option=='--':break
            if wrapper=='env':
                if option.startswith('-S') or option.startswith('--split-string'):raise WrapperError('env command-string options unsupported')
                if option in {'-i','--ignore-environment','-0','--null'}:continue
                if option in {'-u','--unset','-C','--chdir'}:argument(args,option);continue
                if option.startswith(('--unset=','--chdir=')) and option.split('=',1)[1]:continue
            elif wrapper=='command':
                if option=='-p':continue
                if option in {'-v','-V'}:
                    if not args:raise WrapperError('missing command query operand')
                    return Resolved(['command',option,*args],environment)
            elif wrapper=='nice':
                if option in {'-n','--adjustment'}:
                    if not re.fullmatch(r'[+-]?\d+',argument(args,option)):raise WrapperError('nonliteral nice adjustment')
                    continue
                if re.fullmatch(r'-\d+|--adjustment=[+-]?\d+',option):continue
            elif wrapper=='timeout':
                if option in {'--preserve-status','--foreground','--verbose'}:continue
                if option in {'-s','--signal','-k','--kill-after'}:argument(args,option);continue
                if option.startswith(('--signal=','--kill-after=')) and option.split('=',1)[1]:continue
            raise WrapperError('unsupported '+wrapper+' option '+option)
        if wrapper=='timeout':
            duration=argument(args,'duration')
            if not re.fullmatch(r'\d+(?:\.\d+)?[smhd]?',duration):raise WrapperError('nonliteral timeout duration')
        if wrapper=='env':
            assignments()
        if not args:raise WrapperError('missing wrapped executable')
    return Resolved([],environment)
