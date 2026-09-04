from fastapi.testclient import TestClient

from infinity_music.main import app, res

client = TestClient(app)


def test_home_lists_resources():
    response = client.get('/')
    assert response.status_code == 200
    assert 'Infinity Music' in response.text
    assert len(res) >= 1
    assert res[0][0].name in response.text


def test_detail_page_uses_native_player_controls():
    resource_id, _ = res[0]
    response = client.get(f'/detail/{resource_id.name}')
    assert response.status_code == 200
    assert 'controls' in response.text
    assert 'video' in response.text.lower()
