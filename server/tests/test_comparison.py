import time
import pytest
from app.identify.snapshots import get_snapshot_store
from app.identify.masking import mask_spans_with_strategy


def wait(client, task):
    for _ in range(100):
        status = client.get(f'/api/v1/tasks/{task}').json()['status']
        if status in ('done', 'failed'):
            assert status == 'done'
            return
        time.sleep(.03)
    pytest.fail('任务超时')


def test_comparison_same_task_download_and_session(client):
    source = '😀患者电话：13800138000\n正常文本\n再次联系：13800138000'
    item = client.post('/api/v1/tasks/batch', files=[('files', ('sample.txt', source.encode()))]).json()['items'][0]
    task = item['task_id']
    wait(client, task)
    url = f'/api/v1/tasks/{task}/comparison'
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    payload = response.json()
    segment = payload['segments'][0]
    assert segment['original'] == source
    assert len(segment['changes']) == 2
    assert '13800138000' not in segment['masked']
    for change in segment['changes']:
        assert source[change['start']:change['end']] == '13800138000'
        assert segment['masked'][change['masked_start']:change['masked_end']] != '13800138000'
    assert client.get(url + '/download').text == segment['masked']
    stored = client.get(f'/api/v1/tasks/{task}/findings').json()['items'][0]['masked']
    assert stored == segment['masked']
    cookie = client.cookies.get('flowmask_detection_session')
    client.cookies.clear()
    assert client.get(url).status_code == 403
    assert client.get(url + '/download').status_code == 403
    client.cookies.set('flowmask_detection_session', cookie)
    get_snapshot_store().clear()
    assert client.get(url).status_code == 410


def test_comparison_no_hits_and_historical_task(client):
    item = client.post('/api/v1/tasks/batch', files=[('files', ('safe.txt', b'hello\n  world'))]).json()['items'][0]
    wait(client, item['task_id'])
    segment = client.get(f"/api/v1/tasks/{item['task_id']}/comparison").json()['segments'][0]
    assert segment['original'] == segment['masked'] == 'hello\n  world'
    assert segment['changes'] == []
    task = client.post('/api/v1/tasks/text', json={'text': 'hello'}).json()['task_id']
    wait(client, task)
    assert client.get(f'/api/v1/tasks/{task}/comparison').status_code == 410


@pytest.mark.parametrize('strategy', ['partial', 'block', 'hash', 'generalize', 'pseudonym', 'encrypt'])
def test_shared_masker_mapping(strategy):
    text = '😀电话13800138000 与 13900139000'
    spans = [dict(start=3, end=14, type='PHONE'), dict(start=17, end=28, type='PHONE')]
    # 使用实际位置，验证非等长替换和随机加密同次生成的映射。
    spans = [dict(start=text.index(phone), end=text.index(phone) + len(phone), type='PHONE')
             for phone in ('13800138000', '13900139000')]
    mappings = []
    masked = mask_spans_with_strategy(text, spans, strategy, 'test-secret', mappings)
    before = after = 0
    for change in mappings:
        assert text[before:change['start']] == masked[after:change['masked_start']]
        before, after = change['end'], change['masked_end']
    assert text[before:] == masked[after:]


def test_comparison_size_limit(client, monkeypatch):
    import app.tasks.comparison as comparison
    monkeypatch.setattr(comparison, 'MAX_COMPARE_CHARS', 5)
    item = client.post('/api/v1/tasks/batch', files=[('files', ('big.txt', b'hello world'))]).json()['items'][0]
    wait(client, item['task_id'])
    assert client.get(f"/api/v1/tasks/{item['task_id']}/comparison").status_code == 413
