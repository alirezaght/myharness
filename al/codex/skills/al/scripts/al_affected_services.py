"""Structured dependency/coverage facts; repository policy lives in adapters."""
from al_repository_facts import collect,within


def select(root,paths,opaque=(),*,base_oid,ci_context=None,required_adapter=None):
    return collect(root,base_oid=base_oid,paths=paths,opaque=set(opaque),required_adapter=required_adapter)


def implementation_input(path,facts):
    return path.endswith('.py') and any(within(r,path) for u in facts['units'] for r in u['source_roots'])
