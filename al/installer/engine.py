"""Import only the distribution engine bundled with this installer."""
import sys
from pathlib import Path

MODULES = ('al_distribution', 'al_distribution_registry',
           'al_distribution_transaction', 'al_distribution_roles')
SCRIPTS = Path(__file__).resolve().parents[1] / 'codex/skills/al/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    import al_distribution as D
    import al_distribution_registry as R
    import al_distribution_transaction as T
    import al_distribution_roles as Roles
finally:
    # The engine modules are now loaded; leaving this distribution's scripts first on
    # sys.path would make later AL imports in the same process resolve to it.
    sys.path.remove(str(SCRIPTS))
