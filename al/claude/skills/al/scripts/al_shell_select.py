"""Bounded shell startup options select exactly one literal command operand."""

def program(args,error):
    i=1;seen=set()
    if args[0]=='bash':
        while i<len(args) and args[i] in {'--noprofile','--norc'}:
            if args[i] in seen:raise error('repeated Bash startup option')
            seen.add(args[i]);i+=1
    if i>=len(args) or args[i]!='-c' or i+1>=len(args):
        raise error('shell wrapper requires supported startup options and literal -c script')
    if args[i+1]=='-c':raise error('repeated shell startup selector')
    return args[i+1]
