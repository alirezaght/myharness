"""A trusted test host mutates one observation only after a real child proof."""

import json

from al.installer.validator_boot import BOOT
from al.tests.runtime_run_helpers import invoke

DRIVER = (
    BOOT
    + r"""
request=json.loads(sys.argv[1]);captured=_boot(request)
loader=_utility(captured,'validator_loader.py');loader.install_closed_loader(captured,'codex',installers=True)
import installer.cli as cli
import installer.terminal_source as terminal
real=terminal.subprocess.run
completed=[]
def run(argv,*args,**kwargs):
 if terminal.CHILD in argv:
  observed=json.loads(kwargs['input'])
  Path(sys.argv[7]+'.caller-hashes.json').write_text(json.dumps(observed.get('caller_environment'),sort_keys=True))
 result=real(argv,*args,**kwargs)
 if terminal.CHILD in argv:
  assert result.returncode==0,(result.stdout[-2000:],result.stderr[-1000:])
  Path(sys.argv[7]).write_bytes(result.stdout)
  if sys.argv[6]=='environment':os.environ['AL_QUERY_TIME_CHANGED_INPUT']='changed'
  else:Path(sys.argv[6]).write_bytes(b'unreviewed post-child member\n')
  completed.append(True)
 return result
terminal.subprocess.run=run
code=cli.main(['source-proof','--repository',sys.argv[2],'--session',sys.argv[3],'--run',sys.argv[4],'--platform',sys.argv[5]])
assert completed,'the actual child did not return valid proof'
raise SystemExit(code)
"""
)


def post_child_query(launched, platform, terminal, owner, target):
    proof = launched[2].parent / (
        "completed-child-" + ("environment" if target == "environment" else "pin-member") + ".json"
    )
    request = {
        "mode": "snapshot",
        "root": owner["snapshot"],
        "expected": owner["validator_binding"],
    }
    # The real shell preamble establishes PWD/OLDPWD before Python starts.
    driver = proof.with_suffix(".driver")
    preamble = launched[3].read_text().splitlines(keepends=True)[:3]
    assert preamble[0] == "#!/bin/sh\n" and preamble[1].startswith("'''exec' ")
    driver.write_text("".join(preamble) + DRIVER)
    driver.chmod(0o700)
    result = invoke(
        [
            driver,
            json.dumps(request),
            launched[2],
            terminal["session_id"],
            terminal["run_id"],
            platform,
            target,
            proof,
        ],
        launched[2],
        launched[4],
    )
    proof.with_suffix(".stdout").write_text(result.stdout)
    proof.with_suffix(".stderr").write_text(result.stderr)
    return result, proof
