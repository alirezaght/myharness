#!/usr/bin/env python3
"""Read-only shell hook, backed by AL's restricted command parser."""
import json
import sys
import al_shell

# Inline interpreters (python3 -c, node -e) run arbitrary code and stay refused; these pass.
IDIOMS=('Read-only inspection idioms that pass: rg/grep, find (no -delete/-exec writes), '
        'cat/head/tail (tail -n +1 FILE... prints several files with headers), sed -n, wc, jq, '
        'git status/diff/log/show/ls-files, and simple for/while loops whose bodies use only these '
        '(for f in a b; do wc -l "$f"; done). Inline interpreters such as python3 -c are refused; '
        'use jq or the Read/Grep tools instead.')

def deny(reason):
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':'AL read-only agent: '+reason}}))
    raise SystemExit(2)

def main():
    try:
        data=json.loads(sys.stdin.read())
        if not isinstance(data,dict) or not isinstance(data.get('tool_name'),str): deny('malformed hook input')
        if data['tool_name']!='Bash': return
        tool=data.get('tool_input')
        if not isinstance(tool,dict) or not isinstance(tool.get('command'),str): deny('malformed Bash input')
        if 'run_in_background' in tool:
            if type(tool['run_in_background']) is not bool: deny('malformed background metadata')
            if tool['run_in_background']: deny('background execution is prohibited')
        hits=al_shell.check(tool['command'],'readonly')
        if hits: deny('; '.join(hits)+'. '+IDIOMS)
    except (ValueError,TypeError): deny('malformed hook JSON/input')

if __name__=='__main__': main()
