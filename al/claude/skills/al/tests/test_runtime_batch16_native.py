"""Native hook uses the same positional config and shell syntax boundary."""
import json
from pathlib import Path
import subprocess
import sys
import pytest
from test_runtime_batch16_shell import CONFIG_BAD,CONFIG_SAFE,CONTROL,CONTROL_SAFE


@pytest.mark.parametrize('command',CONFIG_BAD+CONFIG_SAFE+CONTROL+CONTROL_SAFE)
def test_native_positional_config_and_control_syntax(command,repo):
    guard=Path(__file__).resolve().parents[1]/'scripts/al_readonly_guard.py'
    result=subprocess.run([sys.executable,str(guard)],cwd=repo,input=json.dumps({'tool_name':'Bash','tool_input':{'command':command}}),capture_output=True,text=True)
    assert result.returncode==(2 if command in CONFIG_BAD+CONTROL else 0),result.stdout+result.stderr
