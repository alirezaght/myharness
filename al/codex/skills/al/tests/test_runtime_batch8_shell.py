"""Real nested executable positions and literal search operands."""
import json
import pytest
from al_shell import check
from hook_regression_helpers import invoke

WRAPPERS=['uv run --','uv --directory . run --project . --no-sync --',
          'pnpm exec --','pnpm --filter web exec --','npm exec --','yarn exec --',
          'uv run -- pnpm exec --','env MODE=test uv run --']


@pytest.mark.parametrize('wrapper',WRAPPERS)
@pytest.mark.parametrize('inner',['git reset --hard','cp owned peer','env GIT_EXTERNAL_DIFF=evil git diff'])
@pytest.mark.parametrize('policy',['readonly','shared'])
def test_nested_mutations_and_environment_overrides_are_rejected(wrapper,inner,policy):
    assert check(wrapper+' '+inner,policy)


@pytest.mark.parametrize('wrapper',WRAPPERS)
@pytest.mark.parametrize('policy',['readonly','shared'])
def test_inner_search_and_its_own_separator_are_untouched(wrapper,policy):
    assert check(wrapper+" rg 'git reset --hard, cp --fix' -- --write",policy)==[]


@pytest.mark.parametrize('command',[
    '-- git reset --hard','env -- -- git reset --hard','uv run --','uv run --project',
    'uv run --project -- git reset --hard','uv run --unknown -- rg harmless',
    'uv run --env-file .env -- git diff','npm exec -c "git reset --hard"',
    'npm exec --package','pnpm exec --unknown -- rg harmless','uv run -m arbitrary',
    'uv run -- -- git reset --hard','uv run --project= -- rg harmless',
])
def test_unsupported_or_dangling_wrapper_forms_fail_closed(command):
    assert check(command,'readonly') and check(command,'shared')


def test_owner_negative_receipt_cannot_advance_or_authorize(cli):
    invoke(cli,'start','--spec','Reject wrapped mutations without advancing workflow')
    before=json.loads(cli('show')[1])
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/readme.md',
           '--engineer-commands','uv run -- git reset --hard',ok=False)
    after=json.loads(cli('show')[1])
    assert after['phase']==before['phase'] and after['completed_phases']==before['completed_phases']
    assert after['write_ownership']['PLANNING']['shared_state']
