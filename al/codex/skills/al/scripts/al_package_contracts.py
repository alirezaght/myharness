"""Finite package parser contracts. Framework versions are contract-qualified, not live certifications."""
import json
import re

VERSIONS={'typescript':{'5.6.3'},'eslint':{'9.17.0'},'vitest':{'2.1.9','3.0.0'},'jest':{'29.7.0','30.0.0'}}
BINARIES={'typescript':('tsc','bin/tsc'),'eslint':('eslint','bin/eslint.js'),'vitest':('vitest','vitest.mjs'),'jest':('jest','bin/jest.js')}
MANAGER_VERSIONS={'npm':{'11.9.0'},'pnpm':set()}  # Only actual offline npm lifecycle qualified.
NODE_VERSIONS={'24.14.0'}  # Genuine offline fixture qualification; other versions stay observable UNKNOWN.


def literal(text):
    """Positive whole-input wire shapes; never evaluate JS or accept by blacklist."""
    try:return json.loads(text)
    except ValueError:pass
    match=re.fullmatch(r'\s*export\s+default\s+([\s\S]+?)\s*;?\s*',text)
    if match:
        try:return json.loads(match[1].removesuffix(';').strip())
        except ValueError:pass
    match=re.fullmatch(r'''\s*import\s*\{\s*defineConfig\s*\}\s*from\s*['"](vitest/config)['"];?\s*export\s+default\s+defineConfig\(([\s\S]+)\)\s*;?\s*''',text)
    if match:
        try:return json.loads(match[2])
        except ValueError:pass
    raise ValueError('configuration is not a qualified finite literal wire shape')


def config(tool,text):
    value=literal(text)
    if tool=='tsc':
        if not isinstance(value,dict) or set(value)-{'compilerOptions','files','include','exclude'}:raise ValueError('unqualified TypeScript dependency/config fields')
        options=value.get('compilerOptions',{})
        allowed={'strict','noEmit','allowJs','checkJs','target','module','moduleResolution','jsx','lib','types','skipLibCheck','esModuleInterop','resolveJsonModule','forceConsistentCasingInFileNames','allowSyntheticDefaultImports','isolatedModules','noUnusedLocals','noUnusedParameters','exactOptionalPropertyTypes','useUnknownInCatchVariables','noFallthroughCasesInSwitch','strictNullChecks','strictFunctionTypes','strictPropertyInitialization','noImplicitAny','noImplicitReturns','noImplicitThis','alwaysStrict'}
        if not isinstance(options,dict) or set(options)-allowed:raise ValueError('unqualified TypeScript compiler option')
        for key,option in options.items():
            if isinstance(option,(dict,list)) and not (key in {'types','lib'} and isinstance(option,list) and all(isinstance(v,str) and '..' not in v for v in option)):raise ValueError('unqualified TypeScript option shape')
            if not isinstance(option,(str,bool,list)):raise ValueError('unqualified TypeScript option value')
        for key in ('files','include','exclude'):
            if key in value and (not isinstance(value[key],list) or any(not isinstance(v,str) or v.startswith('/') or '..' in v.split('/') for v in value[key])):raise ValueError('unqualified TypeScript source closure')
        return value
    if tool=='eslint':
        rows=value if isinstance(value,list) else [value]
        if not rows:raise ValueError('empty ESLint configuration')
        for row in rows:
            if not isinstance(row,dict) or set(row)-{'root','rules','env','globals','parserOptions','files','ignores','ignorePatterns','name'}:raise ValueError('unqualified ESLint plugin/executable configuration')
            if not isinstance(row.get('rules',{}),dict) or any('/' in name for name in row.get('rules',{})):raise ValueError('unqualified ESLint external rule')
            if 'parserOptions' in row and (not isinstance(row['parserOptions'],dict) or set(row['parserOptions'])-{'ecmaVersion','sourceType','ecmaFeatures'}):raise ValueError('unqualified ESLint parser context')
        return value
    if tool=='jest':
        allowed={'testEnvironment','testMatch','testRegex','testPathIgnorePatterns','moduleFileExtensions','clearMocks','resetMocks','restoreMocks','verbose','collectCoverage','coverageDirectory','coverageProvider','rootDir'}
        if not isinstance(value,dict) or set(value)-allowed or value.get('testEnvironment','node')!='node' or value.get('rootDir','.')!='.':raise ValueError('unqualified Jest plugin/transform/environment context')
        return value
    if tool=='vitest':
        if not isinstance(value,dict) or set(value)-{'test','root'} or value.get('root','.')!='.':raise ValueError('unqualified Vitest executable/plugin context')
        test=value.get('test',{})
        if not isinstance(test,dict) or set(test)-{'include','exclude','globals','environment','clearMocks','restoreMocks','mockReset','reporters'} or test.get('environment','node')!='node':raise ValueError('unqualified Vitest test context')
        reporters=test.get('reporters',['default']);reporters=[reporters] if isinstance(reporters,str) else reporters
        if not isinstance(reporters,list) or any(v not in {'default','verbose','basic'} for v in reporters):raise ValueError('unqualified Vitest reporter')
        return value
    raise ValueError('no qualified configuration parser')
