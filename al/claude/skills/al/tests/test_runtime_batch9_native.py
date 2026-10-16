"""The real native readonly hook shares filter and Poetry protections."""
import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('command,allowed',[
 ('git cat-file --batch-command --filters',False),
 ('poetry run -- git reset --hard',False),
 ('poetry run -- env GIT_EXTERNAL_DIFF=evil git diff',False),
 ("poetry run -- rg 'git reset --hard, --filters' -- --write",True),
 ("git log --grep='cat-file --filters'",True),
 ('git cat-file --batch-check="%(objectname) --filters"',True),
])
def test_native_hook_uses_positional_cat_file_and_poetry_rules(repo,command,allowed):
    hook=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    proc=subprocess.run([sys.executable,str(hook)],input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),cwd=repo,text=True,capture_output=True)
    assert (proc.returncode==0)==allowed,(proc.stdout,proc.stderr)
