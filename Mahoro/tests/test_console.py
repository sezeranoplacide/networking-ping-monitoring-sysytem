"""The command console and its fencing.

The console runs whatever it is given through the shell, so the tests that matter
most are the ones proving it cannot be reached unless the desktop launcher turned
it on, and that every run is recorded.
"""
from pathlib import Path

import pytest

import app as flask_app
from ping_monitor import console
from ping_monitor.device_manager import DeviceManager


@pytest.fixture(autouse=True)
def console_disabled():
    """Every test starts with the console off, as a web server has it."""
    console._enabled = False
    yield
    console._enabled = False


@pytest.fixture()
def client(tmp_path: Path):
    manager = DeviceManager(db_path=str(tmp_path / 'console.sqlite3'))
    flask_app.app.config.update(TESTING=True, SECRET_KEY='test-secret')
    flask_app.dm = manager
    with flask_app.app.test_client() as test_client:
        yield test_client, manager


def sign_in(test_client, manager, role='admin'):
    manager.create_user(username='eng', password='Password123', role=role, mfa_enabled=False)
    test_client.get('/login')
    with test_client.session_transaction() as session:
        token = session['csrf_token']
    test_client.post('/login', data={'username': 'eng', 'password': 'Password123',
                                     'csrf_token': token})
    with test_client.session_transaction() as session:
        return {'X-CSRF-Token': session['csrf_token']}


# ---------------------------------------------------------------- fencing


def test_console_is_off_unless_the_desktop_launcher_enables_it() -> None:
    assert console.is_enabled() is False

    with pytest.raises(PermissionError, match='desktop application only'):
        console.run('echo hello')

    console.enable()
    assert console.is_enabled() is True


def test_the_web_endpoint_refuses_while_the_console_is_off(client) -> None:
    """A network-facing deployment must not expose a command endpoint at all."""
    test_client, manager = client
    headers = sign_in(test_client, manager)

    response = test_client.post('/api/console/run',
                                json={'command': 'echo hello'}, headers=headers)

    assert response.status_code == 403
    assert 'desktop' in response.get_json()['error'].lower()


def test_status_explains_why_it_is_unavailable(client) -> None:
    test_client, manager = client
    sign_in(test_client, manager)

    body = test_client.get('/api/console').get_json()

    assert body['enabled'] is False
    assert 'remote code execution' in body['reason']


def test_viewers_cannot_run_commands_even_on_the_desktop(client) -> None:
    test_client, manager = client
    headers = sign_in(test_client, manager, role='viewer')
    console.enable()

    response = test_client.post('/api/console/run',
                                json={'command': 'echo hello'}, headers=headers)

    assert response.status_code == 403


def test_an_empty_command_is_rejected() -> None:
    console.enable()
    for blank in ('', '   ', None):
        with pytest.raises(ValueError, match='Type a command'):
            console.run(blank)


def test_a_bad_working_directory_is_rejected() -> None:
    console.enable()
    with pytest.raises(ValueError, match='is not a directory'):
        console.run('echo hello', cwd='/definitely/not/a/real/path')


# ---------------------------------------------------------------- behaviour


def test_a_command_runs_and_reports_its_output() -> None:
    console.enable()
    result = console.run('echo network-monitor')

    assert result.exit_code == 0
    assert 'network-monitor' in result.output
    assert result.as_dict()['ok'] is True
    assert result.duration_ms >= 0


def test_a_failing_command_reports_its_exit_code() -> None:
    console.enable()
    result = console.run('exit 3')

    assert result.exit_code == 3
    assert result.as_dict()['ok'] is False


def test_a_hanging_command_is_stopped() -> None:
    console.enable()
    # Sleeping via the interpreter running the tests, rather than a shell builtin:
    # `timeout` and `sleep` resolve to different programs depending on what is on
    # PATH, which made this assert on the wrong thing.
    import sys
    sleeper = f'"{sys.executable}" -c "import time; time.sleep(30)"'

    result = console.run(sleeper, timeout=2)

    assert result.timed_out is True
    assert result.exit_code == 124
    assert 'stopped after 2s' in result.output


def test_timeouts_are_capped() -> None:
    console.enable()
    # A caller cannot ask for an unbounded run.
    result = console.run('echo quick', timeout=100_000)
    assert result.duration_ms < console.MAX_TIMEOUT * 1000


def test_every_run_is_written_to_the_audit_log(client) -> None:
    test_client, manager = client
    headers = sign_in(test_client, manager)
    console.enable()

    test_client.post('/api/console/run',
                     json={'command': 'echo audited'}, headers=headers)

    entries = manager.get_command_log()
    assert len(entries) == 1
    assert entries[0]['command'] == 'echo audited'
    assert entries[0]['username'] == 'eng'
    assert entries[0]['exit_code'] == 0
    assert entries[0]['ran_at']


def test_history_is_readable_through_the_api(client) -> None:
    test_client, manager = client
    headers = sign_in(test_client, manager)
    console.enable()

    test_client.post('/api/console/run', json={'command': 'echo one'}, headers=headers)
    test_client.post('/api/console/run', json={'command': 'echo two'}, headers=headers)

    history = test_client.get('/api/console/history').get_json()
    assert [h['command'] for h in history] == ['echo two', 'echo one']


# ---------------------------------------------------------------- palette


def test_the_palette_is_resolved_for_this_platform() -> None:
    sections = console.snippets(gateway='10.0.0.1')

    assert sections, 'the palette should not be empty'
    commands = [item['command'] for section in sections for item in section['items']]
    assert any('10.0.0.1' in c for c in commands), 'gateway should be substituted'
    assert all(item['label'] for section in sections for item in section['items'])


# ---------------------------------------------------------------- first run


def test_a_generated_first_password_is_written_where_it_can_be_read(tmp_path, monkeypatch) -> None:
    """A packaged build seeds its own admin; a password nobody sees locks the
    owner out of their own installation."""
    from ping_monitor import paths
    monkeypatch.setenv('NETMON_DATA_DIR', str(tmp_path))

    manager = DeviceManager(db_path=str(tmp_path / 'first.sqlite3'))
    note = manager.first_run_password_file()

    assert note.exists(), 'the seeded password was never surfaced'
    text = note.read_text(encoding='utf-8')
    assert 'username: admin' in text

    password = next(line.split(': ', 1)[1].strip()
                    for line in text.splitlines() if line.strip().startswith('password:'))
    admin = manager.get_user_by_username('admin')
    from werkzeug.security import check_password_hash
    assert check_password_hash(admin['password_hash'], password), 'the note is wrong'


def test_changing_the_password_removes_the_note(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv('NETMON_DATA_DIR', str(tmp_path))
    manager = DeviceManager(db_path=str(tmp_path / 'first.sqlite3'))
    note = manager.first_run_password_file()

    text = note.read_text(encoding='utf-8')
    password = next(line.split(': ', 1)[1].strip()
                    for line in text.splitlines() if line.strip().startswith('password:'))
    admin = manager.get_user_by_username('admin')

    manager.change_password(admin['id'], current_password=password,
                            new_password='a-much-longer-password')

    assert not note.exists(), 'the note outlived the password it held'


def test_an_explicit_admin_password_writes_no_note(tmp_path, monkeypatch) -> None:
    """Nothing is written to disk when the operator chose the password."""
    monkeypatch.setenv('NETMON_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'chosen-by-the-operator')

    manager = DeviceManager(db_path=str(tmp_path / 'first.sqlite3'))

    assert not manager.first_run_password_file().exists()
