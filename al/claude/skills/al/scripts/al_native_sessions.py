"""Native conversation attribution and serialized active-run pointers."""
import contextlib
import contextvars
import fcntl
import hashlib
import json
import os
import tempfile
import al_store
from al_schema import refuse

_SELECTED=contextvars.ContextVar('native_pointer',default=None)


def conversation():
    value=os.environ.get('CLAUDE_SESSION_ID') or os.environ.get('CLAUDE_CODE_SESSION_ID')
    if value is not None and (not isinstance(value,str) or not value or len(value)>1024):refuse('invalid native conversation identity')
    return value


def pointer(native):
    if native is None:return al_store.CURRENT
    return al_store.RUNTIME_DIR/'conversations'/(hashlib.sha256(native.encode()).hexdigest()+'.json')


def binding(native):
    path=pointer(native)
    if not path.exists() and not path.is_symlink():return None,''
    try:
        if not path.is_file() or path.is_symlink():refuse('invalid native pointer file')
        raw=path.read_bytes()
        if native is None:sid=al_store._sanitise(raw.decode().strip())
        else:
            data=json.loads(raw)
            if not isinstance(data,dict) or set(data)!={'conversation_id','session_id'}:refuse('corrupt native conversation pointer')
            if data['conversation_id']!=native:refuse('native pointer attribution mismatch')
            sid=al_store._sanitise(data['session_id'])
        if not sid:refuse('empty native pointer')
        return raw,sid
    except (OSError,UnicodeError,ValueError,KeyError,TypeError):refuse('corrupt native conversation pointer')


def active(native):return binding(native)[1]


def prepare():
    native=_SELECTED.get();raw,old=binding(native);previous=None
    if old:
        from al_schema import validate_state
        from al_scope import identity
        try:
            previous=json.loads(al_store.state_path(old).read_bytes());validate_state(previous)
            if previous['session_id']!=old or previous.get('native_conversation_id')!=native:refuse('prior run attribution mismatch')
            actual=identity()
            if any(previous['identity'].get(k)!=actual[k] for k in ('root','git_dir','common_dir')):refuse('prior run repository identity mismatch')
        except (OSError,ValueError,KeyError,TypeError):refuse('corrupt or missing prior native run')
    return {'native':native,'raw':raw,'old':old,'status':previous['status'] if previous else None}


@contextlib.contextmanager
def starting(sid):
    context=prepare();directory=al_store.RUNTIME_DIR/sid
    existed=directory.exists();before=set(directory.rglob('*')) if existed else set()
    try:yield context
    except BaseException:
        if active(context['native'])==sid:
            if context['raw'] is None:pointer(context['native']).unlink()
            else:replace_pointer(pointer(context['native']),context['raw'])
        al_store.state_path(sid).unlink(missing_ok=True)
        if directory.exists():
            for p in sorted(set(directory.rglob('*'))-before,key=lambda p:len(p.parts),reverse=True):
                if p.is_symlink() or p.is_file():p.unlink()
                else:p.rmdir()
            if not existed:directory.rmdir()
        raise


@contextlib.contextmanager
def selected(explicit):
    native=conversation() if explicit is None else None
    token=_SELECTED.set(native)
    if native is None:
        try:yield
        finally:_SELECTED.reset(token)
        return
    directory=pointer(native).parent;directory.mkdir(parents=True,exist_ok=True)
    with open(pointer(native).with_suffix('.lock'),'a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN);_SELECTED.reset(token)


def replace_pointer(path,raw):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.pointer-')
    try:
        with os.fdopen(fd,'wb') as out:out.write(raw)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def publish(sid,context):
    native=_SELECTED.get();path=pointer(native)
    if context['native']!=native or binding(native)[0]!=context['raw']:refuse('active pointer changed before publication')
    if not al_store.state_path(sid).is_file():refuse('state must be saved before active pointer')
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps({'conversation_id':native,'session_id':sid})+'\n' if native else sid+'\n'
    replace_pointer(path,raw.encode())
    if context['old'] and context['old']!=sid and context['status']!='APPROVED':
        print('AL active run switched from '+context['old']+' to '+sid+'; previous work remains unfinished')


def clear(sid):
    native=_SELECTED.get();path=pointer(native)
    if active(native)==sid:path.unlink(missing_ok=True)


def attribution():return _SELECTED.get()
