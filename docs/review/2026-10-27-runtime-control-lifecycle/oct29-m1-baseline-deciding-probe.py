import sys,json,pathlib,hashlib
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,'/workspace/Buffalo-Procurement-System/procurement/tools')
import hosted_synthetic_identity as identity
import hosted_synthetic_postgres_runtime as pg
import hosted_synthetic_runtime_regression as driver
ID=identity.DedicatedIdentity('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',950,951,1001,1001)
class AccountReached(Exception):pass
memory={'capture_fit':'REFUSED_MEMORY_ENVELOPE','memory_envelope':{'result':'REFUSED_COMPONENT_ENVELOPE','conservative_sum_bytes':542486528,'service_limit_bytes':536870912}}
reached=False
with patch('hosted_synthetic_capability._require_clean_supervisor'), patch('hosted_synthetic_source.bind_hosted_source',return_value=SimpleNamespace(tree='b'*40)), patch.object(pg,'capture_phase_fit',return_value={'capacity':'FIT'}), patch.object(driver,'run_postgres_fit',return_value=memory) as measured, patch.object(identity.AccountLease,'create',side_effect=AccountReached) as account:
 try:identity.run_provisioned_candidate(ID,source='/fixture',python=str(pathlib.Path('/proc/self/exe').resolve()),repository='repo',event='push',ref='ref',commit='a'*40,diagnostic='postgres-version')
 except AccountReached:reached=True
print(json.dumps({'classification':'INTERNAL CODEX MOCKED M1 CAUSAL REPRODUCTION','capacity':'FIT','memory_diagnostic_result_if_called':memory,'memory_deciding_calls':measured.call_count,'reached_mock_account_create':reached,'mock_account_create_calls':account.call_count,'real_account_mutations':0,'source_sha256':{name:hashlib.sha256(pathlib.Path('/workspace/Buffalo-Procurement-System/procurement/tools',name).read_bytes()).hexdigest() for name in ['hosted_synthetic_identity.py','hosted_synthetic_postgres_runtime.py','hosted_synthetic_runtime_regression.py']}}))
assert reached and account.call_count==1 and measured.call_count==0
