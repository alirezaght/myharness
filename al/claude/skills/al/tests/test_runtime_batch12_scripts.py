"""Only the parsed script family authorizes migration classification."""
import json
import pytest
from al_shell import check
from hook_regression_helpers import invoke


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('script',['migrate','migration','migrations','migrate:wissen','migration:db','migrations:db','migrate:verify','migrations:check','migration:verify:extra'])
@pytest.mark.parametrize('wrapper',['pnpm {}','pnpm run {}','pnpm --filter web run {}','npm --workspace web run {}','yarn workspace web run {}','poetry run env MODE=test pnpm {}'])
def test_migration_family_is_denied_at_every_supported_script_position(policy,script,wrapper):
    assert check(wrapper.format(script),policy)


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('command',[
 'pnpm migrate:wissen --dry-run','pnpm migrate:wissen --check','pnpm migrate:wissen migration:verify',
 'pnpm migration:verify --fix','pnpm migration:verify --update-snapshots',
])
def test_check_arguments_do_not_waive_mutation_or_existing_write_guards(policy,command):
    assert check(command,policy)


@pytest.mark.parametrize('policy',['readonly','shared'])
@pytest.mark.parametrize('command',[
 'pnpm migration:verify','pnpm run format:check','pnpm --filter web run migration:verify',
 'pnpm exec env MODE=test pnpm migration:verify','uv run -- pnpm migration:verify',
 'pnpm --filter migrate:wissen test','pnpm test migrate:wissen',
 'pnpm migrationish','pnpm remigrate','pnpm migrationsafe',
 "rg 'pnpm migrate:wissen --check' .",'git log --grep=migrate:wissen',
])
def test_exact_check_only_exceptions_and_literal_operands_remain_allowed(policy,command):
    assert check(command,policy)==[]


def test_public_ownership_records_denial_without_admitting_work(cli):
    invoke(cli,'start','--spec','Preserve migration ownership in this substantive task.')
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/readme.md','--engineer-commands','pnpm migrate:wissen',ok=False)
    state=json.loads(cli('show')[1]);assert state['write_ownership']['PLANNING']['shared_state']['al-engineer']
    assert state['phase']=='PLANNING' and state['writers']=={}
    invoke(cli,'ownership','--engineer','svc/app.py','--builder','docs/readme.md','--engineer-commands','pnpm migration:verify')
