"""Operator update barriers invariants."""

import pytest

from al.tests.operator_update_helpers import (
    _prefix_cache as _prefix_cache,
    artifact,
    enrolled as enrolled,
    inject,
    load_record,
    package_path,
    public,
    read_json,
    tag,
)


@pytest.mark.parametrize('mutation', ['review', 'source', 'plan', 'config', 'role', 'postimage', 'ownership'])
def test_barriers_refuse_mutable_installation_inputs(enrolled, mutation):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    if mutation == 'review': statement = f"Path({req['operator_review_artifact']['path']!r}).write_text('{{}}')"
    elif mutation == 'source': statement = f"__import__('subprocess').run(['git','-C',{str(w['source'])!r},'tag','-f','v1.1.0','v1.0.0'],check=True,capture_output=True)"
    elif mutation == 'config': statement = f"Path({str(w['home'] / '.codex/config.toml')!r}).write_text('changed = true\\n')"
    elif mutation == 'role': statement = f"Path({str(w['home'] / '.codex/agents/al-architect.toml')!r}).write_text('changed = true\\n')"
    elif mutation == 'postimage': statement = f"Path({str(package_path(target, 'codex') / 'SKILL.md')!r}).write_text('changed')"
    elif mutation == 'ownership':
        path = row['current']['packages']['codex']['path']
        statement = f"write_json({path!r}, dict(read_json({path!r}), generation=999))"
    else: statement = "prepared['plans']['codex']['files'].clear()"
    if mutation == 'plan': inject(enrolled, ['        ' + statement], '        _recheck(prepared)\n', after=False)
    else: inject(enrolled, ["            if platform == 'codex': " + statement])
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 1, result.stdout + result.stderr
    current = load_record(w['state'], target['target_id'])
    assert current['pending'] and current['current'] == row['current']
    assert read_json(w['state'] / 'installer/operations' / current['pending'] / 'journal.json')['status'] == 'FAILED'


def test_historical_progress_between_barriers_is_allowed(enrolled):
    w, target, row, runtime = enrolled
    req = artifact(enrolled, *tag(enrolled))
    path = str(runtime / 'active.json')
    inject(enrolled, [f"            write_json({path!r}, dict(read_json({path!r}), handoffs=read_json({path!r})['handoffs'] + 1))"])
    result = public(enrolled, req, '--allow-running')
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_json(runtime / 'active.json')['handoffs'] == 2
