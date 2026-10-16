"""Positional package scripts and literal executable-wrapper grammars."""
from al_shell import ShellError

OPTIONS={'--filter','-F','--dir','-C','--prefix','--workspace','-w','--cwd','--cache-folder','--registry'}
FLAGS={'--workspace-root','-r','--recursive','--silent','-s','--offline','--ignore-scripts','--frozen-lockfile','--if-present'}
UV_VALUES={'--directory','--project','--python','--color'}
UV_FLAGS={'--no-sync','--no-project','--active','--frozen','--locked','--isolated','--offline','--no-cache','--quiet','-q','--verbose','-v','--no-config','--no-progress'}
MUTATING={'format','generate:contracts','generate','codegen','install','add','remove','uninstall','update','upgrade','sync','ci','link','publish','dev'}


def options(args,flags,values):
    i=0
    while i<len(args) and args[i].startswith('-'):
        token=args[i];i+=1
        if token=='--':break
        if token in flags:continue
        name,sep,value=token.partition('=')
        if name in values:
            if sep:
                if not value:raise ShellError('empty package wrapper operand')
            else:
                if i>=len(args) or not args[i] or args[i].startswith('-'):raise ShellError('missing package wrapper operand for '+name)
                i+=1
            continue
        raise ShellError('unsupported package wrapper option '+token)
    return args[i:]


def script_position(exe,args):
    flags=FLAGS|({'-w'} if exe in {'pnpm','yarn','bun'} else set())
    values=OPTIONS-({'-w'} if exe in {'pnpm','yarn','bun'} else set())
    tail=options(args,flags,values)
    if not tail:raise ShellError('missing package command')
    command,*rest=tail
    if command.startswith('-'):raise ShellError('unsupported package command position')
    if command in {'workspace','workspaces'}:
        if exe!='yarn' or not rest or rest[0].startswith('-'):raise ShellError('unsupported package workspace invocation')
        return script_position(exe,rest[1:])
    if exe in {'pnpm','yarn'} and command=='dlx':raise ShellError('installing dlx dispatch unsupported')
    if command=='run':
        tail=options(rest,{'--if-present','--silent','-s'},set())
        if not tail:raise ShellError('missing package script')
        return tail[0],tail[1:]
    return command,rest


def invocation(exe,args):
    if exe=='poetry':
        if not args or args[0]!='run':raise ShellError('unsupported Poetry invocation')
        nested=args[1:]
        if nested and nested[0]=='--':nested=nested[1:]
        if not nested or not nested[0] or nested[0].startswith('-'):raise ShellError('Poetry executable missing or unsupported option')
        return {'kind':'executable','argv':nested}
    if exe=='uv':
        tail=options(args,UV_FLAGS,UV_VALUES)
        if not tail:raise ShellError('missing uv command')
        command,*rest=tail
    else:command,rest=script_position(exe,args)
    if (exe=='uv' and command=='run') or command=='exec':
        if exe=='uv':flags,values=UV_FLAGS,UV_VALUES
        elif exe in {'pnpm','npm','yarn'}:flags,values={'--silent','-s','--offline','--no'},set()
        else:raise ShellError('unsupported package executable wrapper')
        nested=options(rest,flags,values)
        if not nested or not nested[0] or nested[0]=='--' or nested[0].startswith('-'):raise ShellError('package executable missing')
        return {'kind':'executable','argv':nested}
    if exe=='uv' and command=='pip':return uv_pip(rest)
    return {'kind':'script','script':command,'argv':rest}


def inspect(parsed,policy):
    script=parsed['script']
    if script!='migration:verify' and script.split(':',1)[0] in {'migrate','migration','migrations'}:return 'migration mutation'
    if script in MUTATING or script.startswith(('gen:','contracts:generate','contracts:build','generate:','codegen:')):return 'dependency/generation mutation'
    if policy=='shared' and script=='build':return 'shared build/workspace output'
    return None


def uv_pip(args):
    if not args:raise ShellError('missing uv pip operation')
    operation,*rest=args
    if operation in {'install','sync','uninstall'}:raise ShellError('dependency mutation: uv pip '+operation)
    if operation not in {'list','freeze','show','check'}:raise ShellError('unsupported uv pip operation')
    tail=options(rest,{'--system','--strict','--offline','--no-cache','--quiet','-q','--verbose','-v'}|({'--editable','--exclude-editable'} if operation in {'list','freeze'} else set()),{'--python','--directory','--project'}|({'--format','--exclude'} if operation=='list' else set()))
    if operation=='show':
        if not tail or any(not value or value.startswith('-') for value in tail):raise ShellError('uv pip show requires package operands')
    elif tail:raise ShellError('unsupported uv pip observational operands')
    return {'kind':'script','script':'uv-pip-observe','argv':tail}
