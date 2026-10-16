"""Restricted quote-aware shell parser shared by command policies."""
import re

class ShellError(ValueError):
    pass

def command_list(raw):
    if not raw:return []
    result=[];chunk=[];quote=None;escaped=False
    def flush():
        value=''.join(chunk).strip()
        if value and value not in result:result.append(value)
        chunk.clear()
    for character in raw:
        if escaped:chunk.append(character);escaped=False;continue
        if character=='\\' and quote!="'":chunk.append(character);escaped=True;continue
        if character in "'\"":
            if quote==character:quote=None
            elif quote is None:quote=character
        if character in ',\n' and quote is None:flush()
        else:chunk.append(character)
    flush()
    return result

# Tokens preserve quoted operators as data and recursively expose substitutions.
def tokenize(command):
    tokens=[]; word=''; started=False; literal=False; quote=None; substitutions=[]; i=0
    def flush():
        nonlocal word,started,literal
        if started: tokens.append(('literal' if literal else 'word',word)); word=''; started=False; literal=False
    while i<len(command):
        c=command[i]
        if c=='\\' and quote!="'":
            i+=1
            if i>=len(command): raise ShellError('dangling escape')
            word+=command[i]; started=True;literal=True
        elif c in "'\"":
            if quote==c: quote=None
            elif quote is None: quote=c; started=True;literal=True
            else: word+=c
        elif quote!="'" and command.startswith('$(',i):
            depth=1; j=i+2; q=None
            while j<len(command) and depth:
                x=command[j]
                if x=='\\' and q!="'": j+=2; continue
                if x in "'\"":
                    if q==x:q=None
                    elif q is None:q=x
                elif q is None:
                    if x=='(':depth+=1
                    elif x==')':depth-=1
                j+=1
            if depth: raise ShellError('unclosed executable substitution')
            substitutions.extend(parse(command[i+2:j-1])); word+='${SUBSTITUTION}'; started=True; i=j-1
        elif quote!="'" and c=='`': raise ShellError('backtick substitution unsupported')
        elif quote is None and c in ';&|<>\n':
            flush(); op=c
            if i+1<len(command) and command[i+1]==c and c in '&|<>': op+=c; i+=1
            tokens.append(('op',op))
        elif quote is None and c.isspace(): flush()
        elif quote is None and c in '()': raise ShellError('unsupported shell compound syntax')
        elif quote is None and c=='#' and not started:
            j=command.find('\n',i); i=len(command) if j<0 else j-1
        else: word+=c; started=True
        i+=1
    if quote: raise ShellError('unclosed quote')
    flush()
    return tokens,substitutions

def resolve(argv,find_context=False):
    from al_shell_wrappers import unwrap,WrapperError
    try:return unwrap(argv,find_context)
    except WrapperError as exc:raise ShellError(str(exc)) from exc


def parse(command,find_context=False):
    if not isinstance(command,str) or not command.strip(): raise ShellError('missing shell command')
    tokens,nodes=tokenize(command); argv=[]; redirects=[]; i=0
    def flush():
        nonlocal argv,redirects
        args=resolve(argv[:],find_context)
        if args:
            if args[0] in {'sh','bash','zsh','dash'}:
                from al_shell_select import program as select_program
                program=select_program(args,ShellError)
                if find_context and '{}' in program:raise ShellError('find replacement in shell program')
                nested=parse(program,find_context)
                for node in nested:node['environment']=getattr(args,'environment',[])+node.get('environment',[])
                nodes.extend(nested)
                if redirects:nodes.append({'argv':[':'],'redirects':redirects})
            else:nodes.append({'argv':args,'redirects':redirects,'environment':getattr(args,'environment',[])})
        elif redirects: raise ShellError('redirect without executable')
        argv=[]; redirects=[]
    while i<len(tokens):
        kind,value=tokens[i]
        if kind in {'word','literal'}:
            from al_shell_wrappers import assignment
            if kind=='word' and (not argv or all(assignment(x) for x in argv)) and value in {'if','then','elif','else','fi','for','in','do','done','while','until','case','esac','function','select','coproc','{','}','!','[[',']]'}:raise ShellError('unsupported shell control syntax: '+value)
            argv.append(value)
        elif value in {'>','>>','<','<<'}:
            if value=='<<':raise ShellError('heredocs unsupported')
            if argv and argv[-1].isdigit():argv.pop()
            i+=1
            if i<len(tokens) and tokens[i]==('op','&'):
                i+=1
                if i>=len(tokens) or tokens[i][0] not in {'word','literal'} or not tokens[i][1].isdigit(): raise ShellError('unsupported descriptor redirect')
                redirects.append(('descriptor',tokens[i][1])); i+=1; continue
            if i>=len(tokens) or tokens[i][0] not in {'word','literal'}:raise ShellError('missing redirect target')
            target=tokens[i][1]
            if value in {'>','>>'} and target=='&':raise ShellError('unsupported descriptor redirect')
            redirects.append((value,target))
        elif value in {';','&&','||','|','\n'}:flush()
        else:raise ShellError('background/unsupported operator')
        i+=1
    flush()
    return nodes

GIT_READ={'status','diff','log','show','grep','ls-files','ls-tree','rev-parse','merge-base','cat-file','blame','describe','check-ignore','config'}
FILES={'rm','rmdir','mv','cp','install','touch','mkdir','ln','chmod','chown','chgrp','tee','dd','truncate','patch','shred'}
PACKAGES={'pnpm','npm','yarn','bun','pip','pip3','uv','poetry','cargo','go','mvn','gradle','brew','apt','apt-get'}

def find_actions(args,policy,environment=()):
    import shlex
    operands={'-name','-iname','-path','-ipath','-wholename','-iwholename','-regex','-iregex',
              '-type','-xtype','-size','-maxdepth','-mindepth','-depth','-mtime','-mmin','-atime',
              '-amin','-ctime','-cmin','-newer','-anewer','-cnewer','-newermt','-user','-group',
              '-uid','-gid','-perm','-links','-inum','-samefile','-fstype'}
    plain={'!','-not','-a','-and','-o','-or','(',')','-print','-print0','-prune','-quit','-true',
           '-false','-empty','-readable','-writable','-executable','-nouser','-nogroup','-xdev',
           '-mount','-follow','-daystart','-ignore_readdir_race','-noleaf','-P','-L','-H'}
    out=[];i=0;expression=False
    while i<len(args):
        token=args[i]
        if not expression and token in {'-P','-L','-H'}:i+=1;continue
        if not expression and token not in plain and not token.startswith('-'):
            i+=1;continue
        expression=True
        if token in operands:
            if token=='-depth':i+=1;continue
            if i+1>=len(args):raise ShellError('missing operand for '+token)
            i+=2;continue
        if token in {'-delete','-fprint','-fprint0','-fprintf'}:
            out.append('find file mutation')
            count=2 if token=='-fprintf' else (0 if token=='-delete' else 1)
            if i+count>=len(args):raise ShellError('missing output operand')
            i+=count+1;continue
        if token in {'-exec','-execdir','-ok','-okdir'}:
            end=next((j for j in range(i+1,len(args)) if args[j] in {';','+'}),None)
            if end is None or end==i+1:raise ShellError('unterminated execution action')
            out.extend(check(shlex.join(args[i+1:end]),policy,environment,find_context=True));i=end+1;continue
        if token not in plain:raise ShellError('unsupported expression '+token)
        i+=1
    return out


def check(command,policy,inherited_environment=(),find_context=False):
    try:nodes=parse(command,find_context)
    except ShellError as exc:return ['unsupported shell syntax: '+str(exc)]
    out=[]
    for node in nodes:
        args=node['argv']; exe=args[0]; tail=args[1:]
        for op,target in node['redirects']:
            if op in {'>','>>'} and (policy=='shared' or not (target=='/dev/null' or target.startswith(('/tmp/','/private/tmp/','/var/folders/')))):
                out.append('file redirect')
        reason=None
        if exe=='git':
            try:
                from al_git_policy import inspect,GitError
                sub,rest=inspect(args,tuple(node.get('environment',[]))+tuple(inherited_environment))
            except GitError as exc:out.append(str(exc));continue
            if sub not in GIT_READ:reason='git state change'
        elif exe in FILES:reason='file system mutation'
        elif exe in {'eval','exec','source','.','awk','gawk','xargs'}:reason='dynamic executable unsupported'
        elif exe=='sed' and any(re.match(r'-[^-]*i|--in-place',x) for x in tail):reason='in-place edit'
        elif exe=='find':
            try: out.extend(find_actions(tail,policy,tuple(node.get('environment',[]))+tuple(inherited_environment)))
            except ShellError as exc: out.append('unsupported find grammar: '+str(exc))
        elif exe in PACKAGES:
            try:
                from al_package_policy import invocation,inspect
                parsed=invocation(exe,tail)
                if parsed['kind']=='executable':
                    import shlex
                    out.extend(check(shlex.join(parsed['argv']),policy,tuple(node.get('environment',[]))+tuple(inherited_environment),find_context))
                    continue
                else:reason=inspect(parsed,policy)
            except ShellError as exc:out.append('unsupported package grammar: '+str(exc))
        elif exe in {'codegen','protoc','prisma','graphql-codegen','openapi-generator','alembic','flyway','liquibase'}:reason='generation or migration'
        elif exe in {'prettier','black','gofmt','rustfmt'}:
            if not any(x in {'--check','--list-different','-l','--diff'} for x in tail):reason='formatter write'
        elif exe=='ruff' and 'format' in tail and '--check' not in tail:reason='formatter write'
        elif exe in {'terraform','kubectl','gcloud','aws','az'} and any(x in {'apply','create','delete','destroy','update','set','patch','deploy'} for x in tail):reason='infrastructure mutation'
        elif exe in {'python','python3','node','ruby','perl'}:
            from al_interpreters import inspect
            reason=inspect(exe,tail)
        if exe not in {'rg','grep','egrep','fgrep','git','find'} and any(x in {'--fix','--write','--update-snapshots','--updateSnapshot','-u','-U'} for x in tail):reason='autofix/snapshot write'
        if reason:out.append(reason)
    return out
