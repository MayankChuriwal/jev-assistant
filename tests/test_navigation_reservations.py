"""Owned browser access is gated before initial requests and redirects, without models."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from helpers import find, until

from assistant.browser import ReservationBlocked
from assistant.channel_web import WebChannel
from assistant.core import Core, Task
from assistant.profile import Profile

pytestmark = pytest.mark.browser


@pytest.fixture
def reserved_site(chrome, tmp_path):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.headers['Host'], self.path))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', f'http://localhost:{self.server.server_port}/private')
                self.end_headers()
                return
            body = b'<title>Account</title><p>Account content</p><a href="/redirect" target="_blank">Open redirect</a>'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    a, b = f'http://127.0.0.1:{server.server_port}', f'http://localhost:{server.server_port}'
    core = Core(WebChannel(tmp_path / 'media'), profile=Profile(tmp_path / 'profile.json'))
    holder = Task('holder', 'An unresolved order')
    holder.flow.executions['pending'] = {'commitment': True, 'status': 'unknown'}
    assert core.reserve(holder, b, None)
    yield SimpleNamespace(a=a, b=b, core=core, requests=requests)
    server.shutdown()
    server.server_close()


def open_agent(site, url):
    task = Task('visitor', 'Inspect account history')
    return site.core.agent_for(url, task.goal, task.title, lambda _: {'cancel': True}, lambda _: None, task=task)


@pytest.mark.parametrize('destination', ['initial', 'redirect'])
def test_constructor_blocks_reserved_origin_before_http_access(reserved_site, destination):
    site = reserved_site
    agent = None
    try:
        try:
            agent = open_agent(site, site.b + '/private' if destination == 'initial' else site.a + '/redirect')
        except ReservationBlocked:
            pass
        assert not [r for r in site.requests if r[0].startswith('localhost:')]
        assert agent is None, 'No reserved page may reach the constructor observation/coordinator'
    finally:
        if agent:
            agent.close()


@pytest.mark.parametrize('kind', ['navigation', 'fetch', 'image', 'popup'])
def test_later_redirect_or_fetch_is_blocked_before_reserved_account_request(reserved_site, kind):
    site = reserved_site
    agent = open_agent(site, site.a + '/')
    from assistant.state import put_fact
    put_fact(agent.task, 'alternative_url', site.b + '/', source='user')
    try:
        if kind == 'navigation':
            expression = f"location.href = '{site.a}/redirect'"
        elif kind == 'fetch':
            expression = f"fetch('{site.b}/private').catch(() => {{}})"
        elif kind == 'image':
            expression = f"new Image().src = '{site.b}/private'"
        else:
            page = agent.browser.observe()
            agent.browser.act('CLICK', find(page, 'Open redirect'), page)
            expression = 'true'
        agent.browser.evaluate(expression)
        assert until(lambda: getattr(agent.browser, 'reservation_blocked', False)
                     or any(r[0].startswith('localhost:') for r in site.requests)), (
                         agent.browser.guard_targets, agent.browser.guard_sessions, site.requests,
                         [(t['type'], t['url']) for t in agent.browser.call('Target.getTargets')['targetInfos']])
        assert not [r for r in site.requests if r[0].startswith('localhost:')]
        with pytest.raises(RuntimeError, match='reserved'):
            agent.browser.observe()
    finally:
        agent.close()


def test_nested_cross_origin_frame_cannot_fetch_reserved_account(reserved_site):
    site = reserved_site
    agent = open_agent(site, site.a + '/')
    try:
        child = site.b + '/private'
        agent.browser.evaluate(f"""(() => {{
            const middle = document.createElement('iframe');
            middle.srcdoc = '<iframe src=\"{child}\"></iframe>';
            document.body.append(middle);
        }})()""")
        assert until(lambda: agent.browser.reservation_blocked
                     or any(r[0].startswith('localhost:') for r in site.requests)), (
                         site.requests, agent.browser.guard_targets)
        assert not [r for r in site.requests if r[0].startswith('localhost:')]
        with pytest.raises(ReservationBlocked):
            agent.browser.observe()
    finally:
        agent.close()


@pytest.mark.parametrize('kind', ['link', 'enter', 'redirect', 'javascript'])
def test_provider_gate_prevents_actual_unapproved_destination(reserved_site, kind, monkeypatch):
    site = reserved_site
    site.core.reservations.clear()
    agent = open_agent(site, site.a + '/')
    try:
        if kind in {'link', 'enter'}:
            agent.browser.evaluate(f"document.querySelector('a').href='{site.b}/private';"
                                   "document.querySelector('a').target='';")
            page = agent.browser.observe()
            action = find(page, 'Open redirect')
            if kind == 'enter':
                agent.browser.evaluate("document.querySelector('a').focus()")
                agent.browser.act('PRESS_KEY', action, page, key='Enter')
            else:
                agent.browser.act('CLICK', action, page)
        else:
            url = site.a + '/redirect' if kind == 'redirect' else site.b + '/private'
            agent.browser.evaluate(f"location.href='{url}'")
        assert until(lambda: agent.browser.reservation_blocked or
                     any(r[0].startswith('localhost:') for r in site.requests))
        assert not [r for r in site.requests if r[0].startswith('localhost:')]
        with pytest.raises(RuntimeError):
            agent.browser.observe()
        from unittest.mock import Mock
        resolve = Mock(side_effect=AssertionError('No credential release on an unapproved destination'))
        monkeypatch.setattr(type(agent.secret_store), 'resolve', resolve)
        with pytest.raises(RuntimeError):
            agent.predict()
        resolve.assert_not_called()
    finally:
        agent.browser.close()


@pytest.mark.parametrize('authorization', ['user', 'auth'])
def test_authorized_provider_or_auth_origin_can_redirect(reserved_site, authorization):
    from assistant.state import put_fact
    site = reserved_site
    site.core.reservations.clear()
    agent = open_agent(site, site.a + '/')
    try:
        if authorization == 'user':
            put_fact(agent.task, 'alternative_url', site.b + '/', source='user')
        else:
            agent.task.progress['auth_flows'] = {'login': dict(revision=agent.task.revision,
                status='challenge', allowed_origins=[site.a, site.b], blocked=False)}
        agent.browser.evaluate(f"location.href='{site.a}/redirect'")
        assert until(lambda: any(r[0].startswith('localhost:') for r in site.requests))
        assert not agent.browser.reservation_blocked
    finally:
        agent.browser.close()
