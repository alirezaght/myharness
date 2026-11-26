"""Pure import events preserve ordering, binding semantics and occurrence-based bounds."""
import ast

import pytest

import al_blob_analysis as B


@pytest.mark.parametrize('source,expected', [
    ('import a, b\nfrom .foo import bar\n',
     [['r','a',1,False],['r','b',1,False],['r','pkg.foo',2,False],['m','pkg.foo.bar',2]]),
    ('from importlib import import_module as load\nload("target")\ndef f(load):\n load("shadowed")\n',
     [['r','importlib',1,False],['m','importlib.import_module',1],['r','target',2,True],
      ['i',4,None,'dynamic import binding is shadowed or unsupported']]),
    ('import importlib as il\nalias = il.import_module\nalias(".child", package="pkg")\n',
     [['r','importlib',1,False],['r','pkg.child',3,True]]),
    ('if True:\n import importlib as il\nil.import_module("x")\n',
     [['r','importlib',2,False],['i',3,None,'dynamic import binding is shadowed or unsupported']]),
    ('items = [__import__]\n', [['i',1,None,'import primitive escapes through a container']]),
    ('lambda: __import__("x")\n', [['i',1,None,'lambda dynamic binding context unsupported']]),
    ('sys.path.append("x")\n', [['i',1,None,'runtime search path mutation']]),
    (b'\xff', [['i',None,None,'source AST unavailable']]),
])
def test_ordered_import_and_uncertainty_events(source, expected):
    assert B.import_events(source, 'pkg.mod', False) == expected


def test_complexity_limit_counts_occurrences_including_shared_context(monkeypatch):
    shared = ast.Expr(value=ast.Name(id='x', ctx=ast.Load()))
    tree = ast.Module(body=[shared] * 33333, type_ignores=[])
    assert sum(1 for _ in ast.walk(tree)) == 100000
    monkeypatch.setattr(B, 'parse', lambda data: tree)
    assert B.import_events(b'owned synthetic AST', 'pkg.mod', False) == []
    tree.body.append(ast.Pass())
    assert B.import_events(b'owned synthetic AST', 'pkg.mod', False) == [
        ['i',None,None,'source AST complexity limit']]


def test_deep_binding_recursion_keeps_existing_failure_behavior(monkeypatch):
    value = ast.Constant(value=1)
    for _ in range(3000):value = ast.UnaryOp(op=ast.Not(), operand=value)
    tree = ast.Module(body=[ast.Expr(value=value)], type_ignores=[])
    monkeypatch.setattr(B, 'parse', lambda data: tree)
    with pytest.raises(RecursionError):B.import_events(b'owned deep AST', 'pkg.mod', False)
