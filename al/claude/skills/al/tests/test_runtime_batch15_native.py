"""Native hook shares effective executable and installing-wrapper refusal."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from test_runtime_batch15_shell import DENIED,SAFE,DYNAMIC


@pytest.mark.parametrize('command',DENIED+SAFE+['find . -exec '+x+' \\;' for x in DYNAMIC])
def test_native_find_and_installing_dispatch(command,repo):
    guard=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    result=subprocess.run([sys.executable,str(guard)],cwd=repo,input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),text=True,capture_output=True)
    assert result.returncode==(0 if command in SAFE else 2),result.stdout+result.stderr
