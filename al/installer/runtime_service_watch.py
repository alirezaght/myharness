"""File watcher process of the resident AL service (#27); standard library and ctypes only.

Run as `python -I -S -B -c <this source> <json>` with the verified bytes of this file.
The JSON names `deep` roots (watched recursively), `shallow` roots (the directory and its
`info/` only, where the backend needs a watch per directory) and directory names to `skip`
when adding per-directory watches. Records on stdout are NUL-terminated:

    R<backend>   ready: every root is watched
    E<path>      something at <path> changed
    O<path>      events under <path> may have been lost: treat everything as changed

The process ends when its stdin reaches end of file (its service is gone) or stdout breaks.
macOS uses FSEvents (per-file events, no latency); Linux uses inotify. Elsewhere it reports
no backend and exits, and the service then treats every worktree as changed."""
import ctypes
import ctypes.util
import json
import os
import select
import struct
import sys
import threading


def emit(kind, path=''):
    try:
        os.write(1, kind.encode() + os.fsencode(path) + b'\0')
    except OSError:
        os._exit(0)


def guard_stdin():
    def wait():
        try:
            while os.read(0, 4096):
                pass
        except OSError:
            pass
        os._exit(0)
    threading.Thread(target=wait, daemon=True).start()


def fsevents(roots):
    services = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreServices.framework/CoreServices')
    core = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    pointer = ctypes.c_void_p
    callback_type = ctypes.CFUNCTYPE(None, pointer, pointer, ctypes.c_size_t, pointer,
                                     ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint64))
    core.CFStringCreateWithCString.restype = pointer
    core.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
    core.CFArrayCreate.restype = pointer
    core.CFArrayCreate.argtypes = [pointer, ctypes.POINTER(pointer), ctypes.c_long, pointer]
    core.CFRunLoopGetCurrent.restype = pointer
    services.FSEventStreamCreate.restype = pointer
    services.FSEventStreamCreate.argtypes = [pointer, callback_type, pointer, pointer, ctypes.c_uint64,
                                             ctypes.c_double, ctypes.c_uint32]
    services.FSEventStreamScheduleWithRunLoop.argtypes = [pointer, pointer, pointer]
    services.FSEventStreamStart.argtypes = [pointer]
    services.FSEventStreamStart.restype = ctypes.c_bool
    lost = 0x1 | 0x2 | 0x4 | 0x20 | 0x80  # must-scan, user/kernel dropped, root changed, unmount

    def changed(stream, info, count, paths, flags, ids):
        names = ctypes.cast(paths, ctypes.POINTER(ctypes.c_char_p))
        for index in range(count):
            path = os.fsdecode(names[index])
            emit('O' if flags[index] & lost else 'E', path)

    keep = callback_type(changed)
    strings = (pointer * len(roots))(*[core.CFStringCreateWithCString(None, os.fsencode(root), 0x08000100)
                                       for root in roots])
    array = core.CFArrayCreate(None, strings, len(roots), ctypes.addressof(pointer.in_dll(core, 'kCFTypeArrayCallBacks')))
    # Since now, no latency, no deferral, per-file events, watch the roots themselves.
    stream = services.FSEventStreamCreate(None, keep, None, array, 0xFFFFFFFFFFFFFFFF, 0.0, 0x02 | 0x04 | 0x10)
    if not stream:
        raise OSError('FSEventStreamCreate failed')
    services.FSEventStreamScheduleWithRunLoop(stream, core.CFRunLoopGetCurrent(), pointer.in_dll(core, 'kCFRunLoopDefaultMode'))
    if not services.FSEventStreamStart(stream):
        raise OSError('FSEventStreamStart failed')
    emit('R', 'fsevents')
    core.CFRunLoopRun()


def inotify(deep, shallow, skip):
    libc = ctypes.CDLL(ctypes.util.find_library('c') or None, use_errno=True)
    descriptor = libc.inotify_init1(os.O_CLOEXEC)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), 'inotify_init1 failed')
    mask = 0x2 | 0x4 | 0x8 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800 | 0x02000000
    created, moved_to, overflow, is_dir, ignored = 0x100, 0x80, 0x4000, 0x40000000, 0x8000
    watches = {}

    def add(path, recursive):
        number = libc.inotify_add_watch(descriptor, os.fsencode(path), mask | 0x01000000)
        if number < 0:
            raise OSError(ctypes.get_errno(), 'inotify_add_watch failed: ' + path)
        watches[number] = (path, recursive)
        if not recursive:
            return
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        for entry in entries:
            if entry.is_dir(follow_symlinks=False) and entry.name not in skip:
                add(entry.path, True)

    for root in deep:
        add(root, True)
    for root in shallow:
        add(root, False)
        if os.path.isdir(os.path.join(root, 'info')):
            add(os.path.join(root, 'info'), False)
    emit('R', 'inotify')
    while True:
        ready, _, _ = select.select([0, descriptor], [], [])
        if 0 in ready and not os.read(0, 4096):
            os._exit(0)
        if descriptor not in ready:
            continue
        data = os.read(descriptor, 1 << 16)
        offset = 0
        while offset + 16 <= len(data):
            number, flags, _, length = struct.unpack_from('iIII', data, offset)
            name = data[offset + 16:offset + 16 + length].rstrip(b'\0')
            offset += 16 + length
            if flags & overflow:
                for root in deep + shallow:
                    emit('O', root)
                continue
            base, recursive = watches.get(number, (None, False))
            if base is None:
                continue
            if flags & ignored:
                watches.pop(number, None)
            path = os.path.join(base, os.fsdecode(name)) if name else base
            emit('E', path)
            if recursive and flags & is_dir and flags & (created | moved_to) and os.path.basename(path) not in skip:
                try:
                    add(path, True)
                except OSError:
                    emit('O', path)


def main(argv):
    request = json.loads(argv[1])
    deep, shallow, skip = request['deep'], request['shallow'], set(request['skip'])
    try:
        if sys.platform == 'darwin':
            guard_stdin()
            fsevents(deep + shallow)
        elif sys.platform.startswith('linux'):
            inotify(deep, shallow, skip)
        else:
            emit('X', 'no file watcher backend on ' + sys.platform)
    except (OSError, AttributeError, ValueError) as exc:
        emit('X', str(exc))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
