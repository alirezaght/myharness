"""Shared exact scanner concerns; topic names alone never grant source coverage."""

from harness.classification import INTERVIEW_TOPICS
from harness.interview_catalogue import CatalogueQuestion, catalogue_for
from harness.state_models import QuestionKind, QuestionSource

TESTS_SETUP = "scanner.tests_setup"
DEPENDENCIES = "scanner.dependency_management"
SCANNERS = {
    DEPENDENCIES: (
        "dependencies",
        "No dependency manifest found; how are dependencies managed?",
        "Which dependency mechanism, scope and maintenance approach should this change use?",
    ),
    TESTS_SETUP: (
        "tests",
        "No tests found; which framework and layout should new tests use?",
        "Which test framework and test-file layout should this change use?",
    ),
}


def definitions():
    result = {entry.key: entry for entry in catalogue_for(INTERVIEW_TOPICS)}
    result.update(
        {
            key: CatalogueQuestion(key, topic, prompt, QuestionKind.DECISION)
            for key, (topic, _, prompt) in SCANNERS.items()
        }
    )
    return result


def scanner_concern(question):
    if question.source is not QuestionSource.INVESTIGATION:
        return None
    return next(
        (
            key
            for key, (topic, wording, _) in SCANNERS.items()
            if question.topic == topic and question.text == wording
        ),
        None,
    )
