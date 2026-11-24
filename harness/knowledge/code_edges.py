"""Graph adapter: turn pure ``harness.code_resolution`` edges into knowledge assertions."""

from harness.code_resolution import python_endpoint_edges, script_edges
from harness.knowledge.models import Assertion


def _assert(subject, edges):
    return [
        Assertion(
            subject,
            edge.relation,
            edge.target,
            edge.value,
            edge.text,
            edge.start,
            edge.end,
            origin="static-analysis",
        )
        for edge in edges
    ]


def script_assertions(subject, locator, text):
    return _assert(subject, script_edges(locator, text))


def python_endpoints(tree, subject, raw):
    return _assert(subject, python_endpoint_edges(raw, tree))
