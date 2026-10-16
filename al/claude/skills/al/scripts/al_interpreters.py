"""Bounded startup parsing: only the actual interpreter selector has authority."""

class InvocationError(ValueError):pass


def python(tail):
    flags=set('bBdEhiIOPqRsSuvVx');i=0;exits=False
    while i<len(tail):
        token=tail[i];i+=1
        if token=='--':
            if i>=len(tail) or tail[i]=='-':raise InvocationError('missing script or stdin execution')
            return
        if token=='-':raise InvocationError('stdin interpreter execution')
        if not token.startswith('-'):return
        if token in {'--version','--help','--help-env','--help-xoptions','--help-all'}:exits=True;continue
        if token=='--check-hash-based-pycs':
            if i>=len(tail) or tail[i] not in {'default','always','never'}:raise InvocationError('invalid hash-based pyc option')
            i+=1;continue
        if token.startswith('--'):raise InvocationError('unsupported Python startup option')
        for pos,option in enumerate(token[1:],1):
            if option in {'c','m','W','X'}:
                value=token[pos+1:]
                if not value:
                    if i>=len(tail):raise InvocationError('missing Python option operand')
                    value=tail[i];i+=1
                if not value:raise InvocationError('empty Python option operand')
                if option=='c':raise InvocationError('inline interpreter execution')
                if option=='X' and not (value in {'dev','utf8','utf8=0','utf8=1','faulthandler','importtime','warn_default_encoding','no_debug_ranges'} or value in {'tracemalloc','int_max_str_digits'} or value.startswith(('tracemalloc=','int_max_str_digits=')) and value.split('=',1)[1].isdigit()):raise InvocationError('unsupported Python implementation option')
                if option=='m':
                    if value not in {'pytest','compileall'}:raise InvocationError('unsupported Python module entry point')
                    return
                break
            if option not in flags or option=='i':raise InvocationError('unsupported/interactive Python startup option')
            if option in {'h','V'}:exits=True
    if not exits:raise InvocationError('missing interpreter entry point')


OTHER={
 'node':({'--no-warnings','--trace-warnings','--use-strict','--enable-source-maps'},set(),{'--stack-trace-limit'},set()),
 'ruby':({'--disable-gems'},set('wWvd'),set(),set('IC')),
 'perl':(set(),set('wWTtX'),set(),set('IF')),
}


def other(exe,tail):
    long_flags,flags,long_values,values=OTHER[exe];i=0;exits=False
    while i<len(tail):
        token=tail[i];i+=1
        if token=='--':
            if i>=len(tail) or tail[i]=='-':raise InvocationError('missing script or stdin execution')
            return
        if token=='-':raise InvocationError('stdin interpreter execution')
        if not token.startswith('-'):return
        if token in {'--help','--version'}:exits=True;continue
        name,equals,value=token.partition('=')
        if name in long_flags and not equals:continue
        if name in long_values:
            if not equals:
                if i>=len(tail):raise InvocationError('missing interpreter option operand')
                value=tail[i];i+=1
            if not value:raise InvocationError('empty interpreter option operand')
            continue
        if token.startswith('--'):raise InvocationError('unsupported interpreter startup option')
        for pos,option in enumerate(token[1:],1):
            if option in {'e','E','p','c'}:raise InvocationError('inline/dynamic interpreter execution')
            if option in values:
                if pos==len(token)-1:
                    if i>=len(tail):raise InvocationError('missing interpreter option operand')
                    i+=1
                break
            if option not in flags:raise InvocationError('unsupported interpreter startup option')
    if not exits:raise InvocationError('missing interpreter entry point')


def inspect(exe,tail):
    try:
        if exe in {'python','python3'}:python(tail)
        else:other(exe,tail)
    except InvocationError as exc:return 'dynamic interpreter command unsupported: '+str(exc)
    return None
