"""Native hook rejects uv pip dependency mutations and preserves observations."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from test_runtime_batch14_policy import DENIED,SAFE


@pytest.mark.parametrize('command',DENIED+SAFE)
def test_native_uv_pip_policy(command,repo):
    guard=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    result=subprocess.run([sys.executable,str(guard)],cwd=repo,input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),text=True,capture_output=True)
    assert result.returncode==(2 if command in DENIED else 0),result.stdout+result.stderr
