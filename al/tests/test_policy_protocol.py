"""Closed current-policy response envelopes cannot grant extra authority."""
from copy import deepcopy
import pytest
from al.installer.policy_source import COMMON,EXTRA,response_fields
from al.installer.validator_origin import canonical,digest


def envelope(operation):
    value={key:None for key in COMMON|EXTRA[operation]}
    value.update(schema_version=1,kind='al-current-policy-'+operation+'-proof')
    value['observation_digest']=digest(canonical({k:v for k,v in value.items() if k!='observation_digest'}))
    return value


@pytest.mark.parametrize('operation',['replan','readiness'])
def test_closed_response_fields_and_digest(operation):
    value=envelope(operation);response_fields(value,operation)
    for missing in sorted(value):
        changed=deepcopy(value);changed.pop(missing)
        with pytest.raises(ValueError,match='field'):response_fields(changed,operation)
    for field in ('approved','legacy_validation','skip_coverage','self_reservation'):
        changed=dict(value,**{field:True})
        changed['observation_digest']=digest(canonical({k:v for k,v in changed.items() if k!='observation_digest'}))
        with pytest.raises(ValueError,match='field'):response_fields(changed,operation)
    changed=dict(value,state_sha256='changed')
    with pytest.raises(ValueError,match='digest'):response_fields(changed,operation)


@pytest.mark.parametrize('operation',['replan','readiness'])
def test_schema_and_operation_cannot_be_substituted(operation):
    for key,value in (('schema_version',True),('kind','al-current-policy-other-proof')):
        changed=envelope(operation);changed[key]=value
        with pytest.raises(ValueError,match='schema'):response_fields(changed,operation)
