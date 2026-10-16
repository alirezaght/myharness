"""Established browser source paths for rendered-review applicability."""
from pathlib import PurePosixPath


def rendered_path(path):
    p=PurePosixPath(path);parts=p.parts;suffix=p.suffix.lower()
    if any(x in {'tests','test','__tests__'} for x in parts) or any(x in p.name for x in ('.test.','.spec.')): return False
    script=suffix in {'.ts','.js','.mjs'}
    if script and ('.server.' in p.name or 'server' in parts or 'middleware' in parts): return False
    if parts[:2]==('packages','ui'): return suffix in {'.astro','.tsx','.jsx','.svelte','.vue','.css','.scss','.html','.svg','.png','.jpg','.jpeg','.webp','.gif','.ts','.js','.mjs'}
    if not parts or parts[0]!='apps': return False
    if suffix in {'.astro','.tsx','.jsx','.css','.scss'}: return True
    if suffix not in {'.ts','.js','.mjs'}: return False
    return any(x in {'ui','alpine','client','clients','components','component','scripts'} for x in parts[2:]) or p.name.endswith(('.client.ts','.client.js'))
