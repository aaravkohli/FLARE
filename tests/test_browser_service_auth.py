"""Disposable browser services use private credentials, never the operator's."""
import json
import secrets
import stat

from scripts.swarm_test_services import isolated_auth, write_credentials


def test_isolated_auth_is_unique_and_does_not_inherit_operator_credentials():
    operator = {'AJ_ENV': 'production', 'AJ_ADMIN_USERNAME': 'operator',
                'AJ_ADMIN_PASSWORD': secrets.token_urlsafe(32), 'AJ_SDN_TOKEN': secrets.token_urlsafe(32)}
    original = dict(operator)
    first, env = isolated_auth(operator)
    second, other = isolated_auth(operator)
    assert operator == original
    assert first['username'] != operator['AJ_ADMIN_USERNAME']
    assert first['password'] != operator['AJ_ADMIN_PASSWORD']
    assert first['password'] != second['password']
    assert first['sdnToken'] != second['sdnToken']
    assert env['AJ_SECRET_KEY'] != other['AJ_SECRET_KEY']
    assert env['AJ_ENV'] == 'test'
    assert env['AJ_ADMIN_PASSWORD'] == first['password']
    assert env['AJ_SDN_TOKEN'] == first['sdnToken']
    assert min(len(env[key]) for key in ('AJ_SECRET_KEY', 'AJ_ADMIN_PASSWORD', 'AJ_SDN_TOKEN')) >= 32


def test_browser_and_services_share_explicit_test_credentials(tmp_path):
    first, _ = isolated_auth({})
    filename = write_credentials(tmp_path, first)
    assert json.loads(filename.read_text()) == first
    assert stat.S_IMODE(filename.stat().st_mode) == 0o600
    assert isolated_auth({'FLARE_TEST_CREDENTIALS_FILE': str(filename)})[0] == first
    selected = {'FLARE_TEST_OPERATOR': first['username'], 'FLARE_TEST_PASSWORD': first['password'],
                'FLARE_TEST_SDN_TOKEN': first['sdnToken']}
    assert isolated_auth(selected)[0] == first


def test_explicit_values_override_file_without_changing_it(tmp_path):
    original, _ = isolated_auth({})
    filename = write_credentials(tmp_path, original)
    selected, env = isolated_auth({'FLARE_TEST_CREDENTIALS_FILE': str(filename), 'FLARE_TEST_OPERATOR': 'explicit'})
    assert env['AJ_ADMIN_USERNAME'] == selected['username'] == 'explicit'
    assert selected['password'] == original['password']
    assert json.loads(filename.read_text()) == original
