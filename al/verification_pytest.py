"""Explicit pytest collection/assignment receipts for complete AL verification."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from pathlib import Path
import sys
import time

import pytest
from al.verification_profile import digest, read_json, write_json

STARTED = time.monotonic()
NODES = []
DESELECTED = []
REPORTS = {}
ERRORS = []
SUBPROCESSES = 0


def pytest_addoption(parser):
    parser.addoption('--al-collection-report')
    parser.addoption('--al-assignment')
    parser.addoption('--al-child-report')


def pytest_configure(config):
    if os.environ.get('AL_PROFILE') == '1':
        def audit(event, _args):
            global SUBPROCESSES
            if event == 'subprocess.Popen':SUBPROCESSES += 1
        sys.addaudithook(audit)
    assignment = config.getoption('--al-assignment')
    config._al_assignment = read_json(assignment) if assignment else None
    if config._al_assignment:
        value = config._al_assignment
        if value.get('schema_version') != 1 or value.get('assignment_digest') != digest({k: v for k, v in value.items() if k != 'assignment_digest'}):
            raise pytest.UsageError('invalid verification assignment')
    for name, plugin in config.pluginmanager.list_name_plugin():
        if plugin is None:continue
        module = getattr(plugin, '__name__', plugin.__class__.__module__)
        if not (module.startswith(('_pytest.', 'al.verification_pytest')) or module in {'_pytest', 'pytest', 'builtins'} or name in {'session', 'funcmanage', 'terminalreporter', 'logging-plugin', 'legacypath-tmpdir'} or str(name).endswith('conftest.py')):
            ERRORS.append('unsupported collection plugin: ' + str(name))
    for hook in ('pytest_collection_modifyitems', 'pytest_ignore_collect', 'pytest_collect_file', 'pytest_pycollect_makeitem'):
        for implementation in getattr(config.pluginmanager.hook, hook).get_hookimpls():
            if str(implementation.plugin_name).endswith('conftest.py'):
                ERRORS.append('unsupported repository collection hook: ' + hook)


def _file(path, root):
    path = Path(path).resolve()
    try:name = path.relative_to(root).as_posix()
    except ValueError:name = 'external:' + path.name
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _record(item):
    root = Path(item.config.rootpath)
    fixtures = []
    for name, definitions in sorted(item._fixtureinfo.name2fixturedefs.items()):
        for definition in definitions:
            fn = definition.func
            try:
                code = inspect.getsource(fn)
                source = _file(inspect.getsourcefile(fn), root)
            except (OSError, TypeError):
                code, source = '', {'path': 'unresolved', 'sha256': None}
            fixtures.append({'name': name, 'scope': definition.scope, 'params': digest(re.sub(r'0x[0-9a-fA-F]+', '<address>', repr(definition.params))),
                             'arguments': list(definition.argnames), 'source': source,
                             'implementation': hashlib.sha256(code.encode()).hexdigest(),
                             'dynamic': 'getfixturevalue' in code})
    source = _file(item.path, root)
    return {'nodeid': item.nodeid, 'source': source, 'fixtures': fixtures,
            'parameterization': getattr(getattr(item, 'callspec', None), 'id', None),
            'markers': sorted(marker.name for marker in item.iter_markers()),
            'initial_fixtures': sorted(item._fixtureinfo.initialnames)}


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    NODES.extend(_record(item) for item in items)
    if len({node['nodeid'] for node in NODES}) != len(NODES):ERRORS.append('duplicate collection node')
    assignment = config._al_assignment
    if assignment:
        observed = {n['nodeid']: n for n in NODES}
        expected = {n['nodeid']: n for n in assignment['nodes']}
        if observed != expected:ERRORS.append('assigned collection/source/fixture drift')
    if ERRORS:raise pytest.UsageError('; '.join(ERRORS))


def pytest_deselected(items):
    DESELECTED.extend(item.nodeid for item in items)


def pytest_collectreport(report):
    if report.failed:ERRORS.append('collection failed: ' + report.nodeid)


def pytest_runtest_logreport(report):
    REPORTS.setdefault(report.nodeid, {})[report.when] = {'outcome': report.outcome,
        'wasxfail': bool(getattr(report, 'wasxfail', False)), 'duration': report.duration}


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    value = {'schema_version': 1, 'nodes': NODES, 'deselected': DESELECTED, 'errors': ERRORS,
             'exitstatus': int(exitstatus), 'finalized': True, 'elapsed_seconds': time.monotonic() - STARTED,
             'pytest_version': pytest.__version__, 'reports': REPORTS, 'subprocesses': SUBPROCESSES,
             'launch_token':os.environ.get('AL_VERIFICATION_LAUNCH_TOKEN'),
             'effective_config': {'root': str(config.rootpath), 'inipath': str(config.inipath) if config.inipath else None,
                                  'addopts': config.getini('addopts'), 'autoload_disabled': os.environ.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD') == '1'}}
    assignment = config._al_assignment
    if assignment:
        value.update(run_id=assignment['run_id'], shard_id=assignment['shard_id'], suite=assignment['suite'],
                     assignment_digest=assignment['assignment_digest'], source_digest=assignment['source_digest'])
    profile = sys.modules.get('al_profile')
    value['runtime_profile'] = profile.report() if profile else {}
    path = config.getoption('--al-child-report') or config.getoption('--al-collection-report')
    if path:write_json(path, value)
