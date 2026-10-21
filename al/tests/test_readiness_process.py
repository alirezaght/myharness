"""Observe exact real interpreter/kernel argv without permitting substitutions."""
import json
import os
from pathlib import Path
import subprocess
import sys


PROGRAM=r'''
import ctypes,json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from installer.readiness_reservation import process_arguments
from installer.validator_origin import interpreter_identity,capture
if sys.platform=='darwin':
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    buffer=ctypes.create_string_buffer(4096)
    result=library.proc_pidpath(os.getpid(),buffer,len(buffer))
    if result<=0:raise OSError(ctypes.get_errno(),'kernel executable observation failed')
    kernel=os.fsdecode(buffer.value)
else:kernel=os.readlink('/proc/self/exe')
raw,metadata=capture(Path(kernel).resolve())
print(json.dumps({'argv':process_arguments(os.getpid()),'python_executable':sys.executable,
                  'interpreter':interpreter_identity(),'kernel_executable':kernel,'kernel_metadata':metadata}))
'''


def test_real_process_interpreter_observation(tmp_path):
    root=Path(__file__).resolve().parents[1]
    requested=[sys.executable,'-I','-S','-B','-c',PROGRAM,str(root)]
    result=subprocess.run(requested,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr[-2000:]
    observed=json.loads(result.stdout)
    # Retain source/runtime observations for the exact denied comparison.
    record={**observed,'requested_executable':sys.executable,'requested_tail':['-I','-S','-B','-c','<fixed-probe>',str(root)]}
    path=tmp_path/'interpreter-observation.json';path.write_text(json.dumps(record,indent=2))
    print(json.dumps(record,indent=2))
    from al.installer.interpreter_execution import capture_profile,validate_live
    from al.installer.execution_process import observe
    profile=capture_profile(sys.executable,observed['interpreter'],sys.executable)
    assert profile['image']['path']==observed['kernel_executable']
    assert {k:profile['image'][k] for k in ('sha256','mode')}==observed['kernel_metadata']
    validate_live(profile,observed['interpreter'],observe(os.getpid()))
    (tmp_path/'execution-profile.json').write_text(json.dumps(profile,indent=2))
