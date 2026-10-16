"""Bounded query-only legacy git-config options and literal operand arity."""


def inspect(tail,error):
    scopes={'--local','--global','--system','--worktree'}
    flags={'--name-only','--show-origin','--show-scope','--includes','--no-includes','--null','-z'}
    conversions={'--bool','--int','--bool-or-int','--path','--expiry-date'}
    values={'--file','-f','--blob','--type','--default'}
    operations={'--get':(1,2),'--get-all':(1,2),'--list':(0,0),'-l':(0,0)}
    operation=None;scope=None;conversion=None;default=False;i=0
    while i<len(tail):
        token=tail[i];i+=1
        if token=='--':break
        if not token.startswith('-'):
            i-=1;break
        if token in operations:
            if operation is not None:raise error('conflicting Git config operations')
            operation=token;continue
        if token in flags:continue
        if token in scopes:
            if scope is not None:raise error('conflicting Git config sources')
            scope=token;continue
        if token in conversions:
            if conversion is not None:raise error('conflicting Git config types')
            conversion=token;continue
        if token.startswith('-f') and token!='-f':token='--file='+token[2:]
        name,equals,value=token.partition('=')
        if name not in values or equals and name=='-f':raise error('unsupported Git config option '+token)
        if not equals:
            if i>=len(tail):raise error('Git config option operand missing')
            value=tail[i];i+=1
        if not value:raise error('empty Git config option operand')
        if name in {'--file','-f','--blob'}:
            if scope is not None:raise error('conflicting Git config sources')
            scope=name
        elif name=='--type':
            if conversion is not None or value not in {'bool','int','bool-or-int','path','expiry-date','color'}:raise error('unsupported/conflicting Git config type')
            conversion=value
        else:default=True
    if operation is None:raise error('Git config setter or unsupported operation')
    operands=tail[i:];minimum,maximum=operations[operation]
    if not minimum<=len(operands)<=maximum or operands and not operands[0]:raise error('Git config query operand count')
    if default and operation!='--get':raise error('unsupported Git config default operation')
