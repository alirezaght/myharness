"""The isolated verifier permits the current bounded discovery grammar only."""

import pytest
from al.installer.validator_readonly import _git_arguments


@pytest.mark.parametrize('arguments',[
    ['-c','core.excludesFile=/dev/null','-c','core.untrackedCache=false','ls-files','--cached','-z'],
    ['ls-files','--others','--ignored','--directory','--exclude-per-directory=.gitignore',
     '--exclude=node_modules/','--exclude=**/.venv/','-z'],
    ['ls-tree','-r','-l','-z','a'*40],
])
def test_current_bounded_discovery_is_permitted(arguments):
    assert _git_arguments(arguments)


@pytest.mark.parametrize('arguments',[
    ['-c','core.untrackedCache=true','ls-files','-z'],
    ['-c','core.excludesFile=/other/config','ls-files','-z'],
    ['ls-files','--exclude-per-directory=arbitrary-config','-z'],
    ['ls-files','--exclude=unsafe\nvalue','-z'],
    ['ls-tree','-r','-l','-z','unverified-branch'],
    ['fetch','origin'],
])
def test_unapproved_grammars_still_refuse(arguments):
    assert not _git_arguments(arguments)
