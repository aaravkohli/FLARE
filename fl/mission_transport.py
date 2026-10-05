"""Gate actual Flower update bytes using independently checked simulated receipts."""
import hashlib
import os
import time
import requests
from flwr.common import ndarrays_to_parameters


def request(method,path,body=None):
    url=os.getenv('FLARE_SIMULATION_URL',f"http://{os.getenv('SDN_HOST','127.0.0.1')}:{os.getenv('SDN_PORT','8080')}")
    response=requests.request(method,url+'/simulation'+path,json=body,timeout=5,
        headers={'Authorization':'Bearer '+os.getenv('AJ_SDN_TOKEN','antijam-development-sdn-token')})
    response.raise_for_status()
    return response.json()

def fingerprint(parameters):
    from flwr.common.serde import parameters_to_proto
    serialized=parameters_to_proto(parameters).SerializeToString(deterministic=True)
    return hashlib.sha256(serialized).hexdigest(),len(serialized)

def upload(weights,identity,round_number,session):
    digest,size=fingerprint(ndarrays_to_parameters(weights))
    transfer=request('POST','/fl/transfers',{'client_id':identity,'round':round_number,'session_id':session,'digest':digest,'size_bytes':size})
    while transfer['status']=='pending':
        time.sleep(.2)
        transfer=request('GET','/fl/transfers/'+transfer['transfer_id'])
    return {'mission_session':session,'transfer_id':transfer['transfer_id'],'update_digest':digest,
            'wire_size_bytes':size,'transport_status':transfer['status']}

def verify(fit_result,server_round,session):
    metrics=fit_result.metrics
    digest,size=fingerprint(fit_result.parameters)
    try: receipt=request('GET','/fl/transfers/'+str(metrics.get('transfer_id','')))
    except (requests.RequestException,ValueError): return False,'receipt_unavailable'
    expected={'session_id':session,'round':server_round,'client_id':metrics.get('client_id'),'digest':digest,'size_bytes':size,'status':'delivered'}
    if any(receipt.get(k)!=v for k,v in expected.items()): return False,'receipt_binding_or_delivery_failed'
    return True,None
