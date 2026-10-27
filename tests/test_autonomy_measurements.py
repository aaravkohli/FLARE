"""Independent expected calculations for trace-derived pilot statistics."""
import pytest
from scripts.summarize_autonomous_recovery import cohort_metrics


def test_primary_cohort_metrics_keep_delivered_conditioning_and_real_losses():
    packet=lambda seq,created,completed,status:dict(channel_key=f'flow:{seq}',source='a',destination='base',category='telemetry',created_at=created,completed_at=completed,size_bytes=1000,hop=2,status=status)
    rows=[packet(1,0,.1,'delivered'),packet(2,1,1.3,'delivered'),packet(3,2,3,'dropped')]
    result=cohort_metrics(rows)
    assert result['primary_generated']==3 and result['primary_dropped']==1
    assert result['primary_pdr']==pytest.approx(2/3)
    assert result['primary_latency_ms']==pytest.approx(200)
    assert result['primary_jitter_ms']==pytest.approx(200)
    assert result['primary_hops']==2
    assert result['primary_throughput_mbps']==pytest.approx(2000*8/180/1e6)
    assert cohort_metrics(list(reversed(rows)))['primary_offered_sha256']==result['primary_offered_sha256']


def test_absent_primary_evidence_is_not_a_zero_latency_or_perfect_delivery():
    result=cohort_metrics([])
    assert result['primary_pdr'] is None and result['primary_latency_ms'] is None
    assert result['primary_jitter_ms'] is None and result['primary_hops'] is None
    assert result['primary_generated']==0 and result['primary_throughput_mbps']==0


def test_receipt_episode_reduction_requires_three_second_loss_and_five_stable_seconds(tmp_path):
    import gzip,json
    from scripts.summarize_autonomous_recovery import receipt_episodes
    trace=tmp_path/'fixture.jsonl.gz';node='sim_uav_001'
    with gzip.open(trace,'wt') as f:
        for at in [.1]+[10.1+i for i in range(11)]:
            f.write(json.dumps(dict(kind='packet_result',data=dict(category='routing',status='delivered',channel_key=f'autonomy:response:base:{node}:False:0:1:mesh',completed_at=at,destination=node)))+'\n')
    episodes,primary=receipt_episodes(trace);own=[e for e in episodes if e['node']==node]
    assert len(own)==2
    assert own[0]['disconnected_at']==pytest.approx(3.15)
    assert own[0]['reconnected_at']==pytest.approx(15.15)
    assert own[1]['disconnected_at']==pytest.approx(23.15) and own[1]['reconnected_at'] is None
    assert primary['primary_generated']==0
