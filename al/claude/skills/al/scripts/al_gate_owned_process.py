"""Standalone gate child capability backed by private receipts and kernel identity."""
from pathlib import Path
import os

from installer.owned_process import OwnedProcess


class OwnedGateProcess:
    def __init__(self, log, token):
        self.owner=OwnedProcess(Path(log).parent/('.gate-owned-'+token),token)
        self.cleanup=None;self.args=None

    @property
    def pid(self):
        return self.owner.process.pid if self.owner.process is not None else None

    @property
    def returncode(self):
        return self.owner.process.returncode if self.owner.process is not None else None

    @property
    def stdout(self):
        return self.owner.process.stdout if self.owner.process is not None else None

    @stdout.setter
    def stdout(self,value):
        self.owner.process.stdout=value

    @property
    def stderr(self):
        return self.owner.process.stderr if self.owner.process is not None else None

    @stderr.setter
    def stderr(self,value):
        self.owner.process.stderr=value

    @property
    def stdin(self):
        return self.owner.process.stdin if self.owner.process is not None else None

    @property
    def secondary_signals(self):
        return list((self.cleanup or {}).get('secondary_signals',()))

    def spawn(self,argv,*,cwd=None,env=None,stdin=None,stdout=None,stderr=None,pass_fds=(),start_new_session=True):
        if start_new_session is not True:raise ValueError('gate child requires its private session')
        self.args=argv
        if isinstance(argv,(str,bytes,os.PathLike)):argv=[argv]
        return self.owner.spawn_streams(argv,cwd=cwd,env=env,stdin=stdin,stdout=stdout,stderr=stderr,pass_fds=pass_fds)

    def poll(self):
        return self.owner.process.poll()

    def wait(self,timeout=None):
        return self.owner.process.wait(timeout=timeout)

    def communicate(self,input=None,timeout=None):
        return self.owner.process.communicate(input=input,timeout=timeout)

    def cancel(self):
        if self.cleanup is None:self.cleanup=self.owner.cleanup()
        if not self.cleanup['complete']:
            for stream in (self.stdin,self.stdout,self.stderr):
                if stream is not None and not stream.closed:stream.close()
            raise OSError('owned gate child cleanup is unresolved')
        if self.stdin is not None and not self.stdin.closed:self.stdin.close()
        return self.returncode

    def completion(self):
        if self.cleanup is None:
            if self.owner.process is not None and self.owner.process.poll() is None:
                return {'exit':None,'complete':False,'group_absent':False,'errors':[],
                        'unresolved':True,'secondary_signals':[], 'descendant_scope':'group',
                        'descendants_settled':None,'settlement_basis':'owned-process-group-only'}
            self.cleanup=self.owner.cleanup()
        proof=dict(self.cleanup)
        return dict(proof,exit=self.returncode,group_absent=proof['owned_group_settled'],
                    unresolved=bool(proof['unresolved']))
