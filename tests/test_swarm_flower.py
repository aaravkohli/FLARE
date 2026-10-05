"""Update receipt and reset boundaries; modeled transport is never real RF proof."""
from types import SimpleNamespace
import numpy as np
from flwr.common import Parameters, FitRes, Status, Code, ndarrays_to_parameters
from flwr.common.serde import parameters_to_proto
from fl.mission_transport import fingerprint


def test_digest_and_size_cover_actual_serialized_parameters():
    parameters=Parameters(tensors=[b'abc',b'123456'],tensor_type='numpy.ndarray')
    assert fingerprint(parameters)[1]==len(parameters_to_proto(parameters).SerializeToString(deterministic=True))
    assert fingerprint(parameters)!=fingerprint(Parameters(tensors=parameters.tensors,tensor_type='different'))


def test_reset_during_aggregation_restores_weights_and_trust(monkeypatch,tmp_path):
    import fl.server as server
    import fl.mission_transport as transport
    from fl.model import build_model,get_model_weights
    monkeypatch.setenv('FLARE_SWARM_ENABLED','1')
    for key,name in [('FLARE_FL_TRUST_STATE','trust.json'),('FLARE_FL_METRICS_JSON','metrics.json'),('FLARE_FL_METRICS_CSV','metrics.csv')]:monkeypatch.setenv(key,str(tmp_path/name))
    baseline=get_model_weights(build_model(server._MODEL_CFG))
    strategy=server.SecureFedAvgV2(initial_parameters=ndarrays_to_parameters(baseline),checkpoint_output_path=tmp_path/'candidate.pth')
    strategy._mission_session='original';phase={'session':'original'}
    monkeypatch.setattr(transport,'verify',lambda *_:(True,None))
    monkeypatch.setattr(transport,'request',lambda *args:{'session_id':phase['session']})
    def reset_during_evaluation(*_):
        phase['session']='reset'
        return {'threat_f1':.5}
    monkeypatch.setattr(strategy,'_evaluate_on_test_set',reset_during_evaluation)
    results=[]
    for index in range(1,4):
        weights=[w+np.full_like(w,.0001*index) for w in baseline]
        results.append((SimpleNamespace(cid=f'connection-{index}'),FitRes(status=Status(code=Code.OK,message=''),parameters=ndarrays_to_parameters(weights),num_examples=500,metrics={'client_id':f'drone_{index}','train_loss':.1})))
    parameters,metrics=strategy.aggregate_fit(1,results,[])
    assert parameters is None and metrics['invalidated_session']==1
    assert all(np.array_equal(old,new) for old,new in zip(baseline,strategy._global_weights))
    assert strategy._trust.snapshot()==[]
    assert not (tmp_path/'metrics.json').exists()
