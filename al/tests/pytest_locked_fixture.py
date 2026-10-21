"""Offline wheels containing actual installed pytest and dependency bytes."""
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import zipfile

PACKAGES=('pytest','iniconfig','packaging','pluggy','pygments')


def wheelhouse(directory):
    directory=Path(directory);directory.mkdir();provenance=[]
    for name in PACKAGES:
        dist=importlib.metadata.distribution(name);files={};sources=[]
        for item in dist.files or []:
            path=Path(item)
            if path.is_absolute() or '..' in path.parts or path.suffix in {'.pyc','.pyo'} or path.name=='RECORD':continue
            source=Path(dist.locate_file(item));before=source.stat();raw=source.read_bytes();after=source.stat()
            if (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('pytest wheel input moved')
            files[path.as_posix()]=raw;sources.append({'path':str(source),'sha256':hashlib.sha256(raw).hexdigest()})
        metadata=next(p for p in files if p.endswith('.dist-info/WHEEL'));tags=[line[5:] for line in files[metadata].decode().splitlines() if line.startswith('Tag: ')]
        if not tags or any(not tag.endswith('-none-any') for tag in tags):raise ValueError('fixture requires pure actual pytest dependency wheels')
        tag=tags[0];stem=name.replace('-','_')+'-'+dist.version
        record=metadata.rsplit('/',1)[0]+'/RECORD';stream=io.StringIO();writer=csv.writer(stream,lineterminator='\n')
        for path,raw in sorted(files.items()):writer.writerow([path,'sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode(),len(raw)])
        writer.writerow([record,'','']);files[record]=stream.getvalue().encode()
        output=directory/(stem+'-'+tag+'.whl')
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path,raw in sorted(files.items()):archive.writestr(path,raw)
        provenance.append({'name':name,'version':dist.version,'wheel':str(output),'wheel_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'sources':sources})
    (directory.parent/'pytest-wheels-provenance.json').write_text(json.dumps(provenance,indent=2))
    return directory
