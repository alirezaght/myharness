"""A prepared classification is never an ordinary native/check read exception."""

import pytest

from harness.execution_contract import canonical
from harness.template_classification import validate_reference


def test_unissued_prepared_reference_is_not_generic_authority(tmp_path):
    path = tmp_path / "grant.json"
    path.write_text(canonical({"kind": "prepared-exact-template-classification-v1", "context": {}}))
    path.chmod(0o600)
    import hashlib

    reference = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match="policy refused"):
        validate_reference(reference)
