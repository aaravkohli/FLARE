"""Opacus keeps the same Flower/checkpoint contract as the public model."""
import numpy as np
import pytest
import torch
from fl.model import build_model,get_model_weights,set_model_weights


def test_opacus_lstm_aliases_never_change_wire_parameter_order_or_count():
    from opacus.validators import ModuleValidator
    from opacus.grad_sample import GradSampleModule
    model=build_model({'hidden_size':8,'num_layers':2,'dropout':0.})
    baseline=[w.copy() for w in get_model_weights(model)]
    private=GradSampleModule(ModuleValidator.fix(model))
    exported=get_model_weights(private)
    assert [w.shape for w in exported]==[w.shape for w in baseline]
    assert all(np.array_equal(a,b) for a,b in zip(exported,baseline))
    updated=[w+np.float32(.125) for w in baseline]
    set_model_weights(private,updated)
    assert all(np.array_equal(a,b) for a,b in zip(get_model_weights(private),updated))
    restored=build_model({'hidden_size':8,'num_layers':2,'dropout':0.})
    set_model_weights(restored,get_model_weights(private))
    assert all(np.array_equal(a,b) for a,b in zip(get_model_weights(restored),updated))


def test_private_client_compression_two_rounds_keep_server_shapes(monkeypatch):
    import fl.client as client
    monkeypatch.delenv('FLARE_SWARM_ENABLED',raising=False)
    monkeypatch.setitem(client._TRAIN_CFG,'local_epochs',1);monkeypatch.setitem(client._TRAIN_CFG,'batch_size',4)
    monkeypatch.setitem(client._MODEL_CFG,'hidden_size',8);monkeypatch.setitem(client._MODEL_CFG,'num_layers',1);monkeypatch.setitem(client._MODEL_CFG,'dropout',0.)
    worker=client.DroneFlClient('audit-private',enable_dp=True,enable_compression=True,enable_personalization=False)
    worker.X=worker.X[:8];worker.y_threat=worker.y_threat[:8];worker.y_attack=worker.y_attack[:8]
    parameters=[w.copy() for w in get_model_weights(worker.model)];shapes=[w.shape for w in parameters];epsilon=0
    for round_number in (1,2):
        updated,samples,metrics=worker.fit(parameters,{'total_rounds':2,'server_round':round_number})
        assert [w.shape for w in updated]==shapes
        assert samples==8 and metrics['dp_enabled']==1
        assert metrics['dp_epsilon']>epsilon;epsilon=metrics['dp_epsilon']
        assert all(np.isfinite(w).all() for w in updated)
        parameters=updated


def test_invalid_wire_parameters_are_rejected_before_any_model_mutation():
    model=build_model({'hidden_size':8,'num_layers':1,'dropout':0.})
    baseline=get_model_weights(model)
    malformed=[w+1 for w in baseline];malformed[-1]=np.zeros((999,),dtype=np.float32)
    with pytest.raises(ValueError,match='requires shape'):set_model_weights(model,malformed)
    assert all(np.array_equal(a,b) for a,b in zip(baseline,get_model_weights(model)))
    with pytest.raises(ValueError,match='parameter arrays'):set_model_weights(model,baseline[:-1])
