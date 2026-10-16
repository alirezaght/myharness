"""Native hook inherits executable-wrapper validation without a workaround."""
import json
from pathlib import Path
import subprocess
import sys


def test_native_hook_uses_the_same_wrapper_grammar(repo):
    hook=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    for command,allowed in [('uv run -- git reset --hard',False),('npm exec -- cp owned peer',False),
                            ("uv run -- rg 'git reset --hard' -- --write",True)]:
        proc=subprocess.run([sys.executable,str(hook)],input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),cwd=repo,text=True,capture_output=True)
        assert (proc.returncode==0)==allowed,(command,proc.stdout,proc.stderr)
