# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import inspect
import mock
import pytest
import raven
import time
from socket import socket, AF_INET, SOCK_DGRAM
from raven.base import Client, ClientState
from raven.transport import AsyncTransport
from raven.utils.stacks import iter_stack_frames
from raven.utils import six
from raven.utils.testutils import TestCase


class TempStoreClient(Client):
    def __init__(self, servers=None, **kwargs):
        self.events = []
        super(TempStoreClient, self).__init__(servers=servers, **kwargs)

    def is_enabled(self):
        return True

    def send(self, **kwargs):
        self.events.append(kwargs)


class ClientStateTest(TestCase):
    def test_should_try_online(self):
        state = ClientState()
        assert state.should_try()

    def test_should_try_new_error(self):
        state = ClientState()
        state.status = state.ERROR
        state.last_check = time.time()
        state.retry_number = 1
        assert not state.should_try()

    def test_should_try_time_passed_error(self):
        state = ClientState()
        state.status = state.ERROR
        state.last_check = time.time() - 10
        state.retry_number = 1
        assert state.should_try()

    def test_set_fail(self):
        state = ClientState()
        state.set_fail()
        assert state.status == state.ERROR
        assert state.last_check != None
        assert state.retry_number == 1

    def test_set_success(self):
        state = ClientState()
        state.status = state.ERROR
        state.last_check = 'foo'
        state.retry_number = 0
        state.set_success()
        assert state.status == state.ONLINE
        assert state.last_check == None
        assert state.retry_number == 0


class ClientTest(TestCase):
    def setUp(self):
        self.client = TempStoreClient()

    def test_first_client_is_singleton(self):
        from raven import base
        base.Raven = None

        client = Client()
        client2 = Client()

        assert base.Raven is client
        assert client is not client2

    @mock.patch('raven.transport.base.HTTPTransport.send')
    @mock.patch('raven.base.ClientState.should_try')
    def test_send_remote_failover(self, should_try, send):
        should_try.return_value = True

        client = Client(
            servers=['http://example.com'],
            public_key='public',
            secret_key='secret',
            project=1,
        )

        # test error
        send.side_effect = Exception()
        client.send_remote('http://example.com/api/store', 'foo')
        assert client.state.status == client.state.ERROR

        # test recovery
        send.side_effect = None
        client.send_remote('http://example.com/api/store', 'foo')
        assert client.state.status == client.state.ONLINE

    @mock.patch('raven.base.Client._registry.get_transport')
    @mock.patch('raven.base.ClientState.should_try')
    def test_async_send_remote_failover(self, should_try, get_transport):
        should_try.return_value = True
        async_transport = AsyncTransport()
        async_transport.async_send = async_send = mock.Mock()
        get_transport.return_value = async_transport

        client = Client(
            servers=['http://example.com'],
            public_key='public',
            secret_key='secret',
            project=1,
        )

        # test immediate raise of error
        async_send.side_effect = Exception()
        client.send_remote('http://example.com/api/store', 'foo')
        assert client.state.status == client.state.ERROR

        # test recovery
        client.send_remote('http://example.com/api/store', 'foo')
        success_cb = async_send.call_args[0][2]
        success_cb()
        assert client.state.status == client.state.ONLINE

        # test delayed raise of error
        client.send_remote('http://example.com/api/store', 'foo')
        failure_cb = async_send.call_args[0][3]
        failure_cb(Exception())
        assert client.state.status == client.state.ERROR

    @mock.patch('raven.base.Client.send_remote')
    @mock.patch('raven.base.time.time')
    def test_send(self, time, send_remote):
        time.return_value = 1328055286.51
        client = Client(
            servers=['http://example.com'],
            public_key='public',
            secret_key='secret',
            project=1,
        )
        client.send(**{
            'foo': 'bar',
        })
        send_remote.assert_called_once_with(
            url='http://example.com',
            data=six.b('eJyrVkrLz1eyUlBKSixSqgUAIJgEVA=='),
            headers={
                'User-Agent': 'raven-python/%s' % (raven.VERSION,),
                'Content-Type': 'application/octet-stream',
                'X-Sentry-Auth': (
                    'Sentry sentry_timestamp=1328055286.51, '
                    'sentry_client=raven-python/%s, sentry_version=2.0, '
                    'sentry_key=public, '
                    'sentry_secret=secret' % (raven.VERSION,))
            },
        )

    @mock.patch('raven.base.Client.send_remote')
    @mock.patch('raven.base.time.time')
    def test_send_with_auth_header(self, time, send_remote):
        time.return_value = 1328055286.51
        client = Client(
            servers=['http://example.com'],
            public_key='public',
            secret_key='secret',
            project=1,
        )
        client.send(auth_header='foo', **{
            'foo': 'bar',
        })
        send_remote.assert_called_once_with(
            url='http://example.com',
            data=six.b('eJyrVkrLz1eyUlBKSixSqgUAIJgEVA=='),
            headers={
                'User-Agent': 'raven-python/%s' % (raven.VERSION,),
                'Content-Type': 'application/octet-stream',
                'X-Sentry-Auth': 'foo'
            },
        )

    def test_encode_decode(self):
        data = {'foo': 'bar'}
        encoded = self.client.encode(data)
        assert isinstance(encoded, str)
        assert data == self.client.decode(encoded)

    def test_dsn(self):
        client = Client(dsn='http://public:secret@example.com/1')
        assert client.servers == ['http://example.com/api/1/store/']
        assert client.project == '1'
        assert client.public_key == 'public'
        assert client.secret_key == 'secret'

    def test_dsn_as_first_arg(self):
        client = Client('http://public:secret@example.com/1')
        assert client.servers == ['http://example.com/api/1/store/']
        assert client.project == '1'
        assert client.public_key == 'public'
        assert client.secret_key == 'secret'

    def test_slug_in_dsn(self):
        client = Client('http://public:secret@example.com/slug-name')
        assert client.servers == ['http://example.com/api/slug-name/store/']
        assert client.project == 'slug-name'
        assert client.public_key == 'public'
        assert client.secret_key == 'secret'

    def test_get_public_dsn(self):
        client = Client('threaded+http://public:secret@example.com/1')
        public_dsn = client.get_public_dsn()
        assert public_dsn == '//public@example.com/1'

    def test_get_public_dsn_override_scheme(self):
        client = Client('threaded+http://public:secret@example.com/1')
        public_dsn = client.get_public_dsn('https')
        assert public_dsn == 'https://public@example.com/1'

    def test_explicit_message_on_message_event(self):
        self.client.captureMessage(message='test', data={
            'message': 'foo'
        })

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'foo'

    def test_explicit_message_on_exception_event(self):
        try:
            raise ValueError('foo')
        except:
            self.client.captureException(data={'message': 'foobar'})

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'foobar'

    def test_exception_event(self):
        try:
            raise ValueError('foo')
        except:
            self.client.captureException()

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'ValueError: foo'
        assert 'sentry.interfaces.Exception' in event
        exc = event['sentry.interfaces.Exception']
        assert exc['type'] == 'ValueError'
        assert exc['value'] == 'foo'
        assert exc['module'] == ValueError.__module__  # this differs in some Python versions
        assert 'sentry.interfaces.Stacktrace' in event
        frames = event['sentry.interfaces.Stacktrace']
        assert len(frames['frames']) == 1
        frame = frames['frames'][0]
        assert frame['abs_path'] == __file__.replace('.pyc', '.py')
        assert frame['filename'] == 'tests/base/tests.py'
        assert frame['module'] == __name__
        assert frame['function'] == 'test_exception_event'
        assert 'timestamp' in event

    def test_message_event(self):
        self.client.captureMessage(message='test')

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'test'
        assert 'sentry.interfaces.Stacktrace' not in event
        assert 'timestamp' in event

    def test_exception_context_manager(self):
        cm = self.client.context(tags={'foo': 'bar'})
        try:
            with cm:
                raise ValueError('foo')
        except:
            pass
        else:
            self.fail('Exception should have been raised')

        assert cm.result != None

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'ValueError: foo'
        assert 'sentry.interfaces.Exception' in event
        exc = event['sentry.interfaces.Exception']
        assert exc['type'] == 'ValueError'
        assert exc['value'] == 'foo'
        assert exc['module'] == ValueError.__module__  # this differs in some Python versions
        assert 'sentry.interfaces.Stacktrace' in event
        frames = event['sentry.interfaces.Stacktrace']
        assert len(frames['frames']) == 1
        frame = frames['frames'][0]
        assert frame['abs_path'] == __file__.replace('.pyc', '.py')
        assert frame['filename'] == 'tests/base/tests.py'
        assert frame['module'] == __name__
        assert frame['function'] == 'test_exception_context_manager'
        assert 'timestamp' in event

    def test_stack_explicit_frames(self):
        def bar():
            return inspect.stack()

        frames = bar()

        self.client.captureMessage('test', stack=iter_stack_frames(frames))

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'test'
        assert 'sentry.interfaces.Stacktrace' in event
        assert len(frames) == len(event['sentry.interfaces.Stacktrace']['frames'])
        for frame, frame_i in zip(frames, event['sentry.interfaces.Stacktrace']['frames']):
            assert frame[0].f_code.co_filename == frame_i['abs_path']
            assert frame[0].f_code.co_name == frame_i['function']

    def test_stack_auto_frames(self):
        self.client.captureMessage('test', stack=True)

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['message'] == 'test'
        assert 'sentry.interfaces.Stacktrace' in event
        assert 'timestamp' in event

    def test_site(self):
        self.client.captureMessage(message='test', data={'site': 'test'})

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert 'site' in event['tags']
        assert event['tags']['site'] == 'test'

    def test_implicit_site(self):
        self.client = TempStoreClient(site='foo')
        self.client.captureMessage(message='test')

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert 'site' in event['tags']
        assert event['tags']['site'] == 'foo'

    def test_logger(self):
        self.client.captureMessage(message='test', data={'logger': 'test'})

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['logger'] == 'test'
        assert 'timestamp' in event

    def test_tags(self):
        self.client.captureMessage(message='test', tags={'logger': 'test'})

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        assert event['tags'] == {'logger': 'test'}

    def test_client_extra_context(self):
        self.client.extra = {
            'foo': 'bar',
            'logger': 'baz',
        }
        self.client.captureMessage(message='test', extra={'logger': 'test'})

        assert len(self.client.events) == 1
        event = self.client.events.pop(0)
        if six.PY3:
            expected = {'logger': "'test'", 'foo': "'bar'"}
        else:
            expected = {'logger': "u'test'", 'foo': "u'bar'"}
        assert event['extra'] == expected


# TODO: Python 3
@pytest.mark.skipif(str("six.PY3"))
class ClientUDPTest(TestCase):
    def setUp(self):
        self.server_socket = socket(AF_INET, SOCK_DGRAM)
        self.server_socket.bind(('127.0.0.1', 0))
        self.client = Client(servers=["udp://%s:%s" % self.server_socket.getsockname()], key='BassOmatic')

    def test_delivery(self):
        self.client.captureMessage('test')
        data, address = self.server_socket.recvfrom(2 ** 16)
        assert "\n\n" in data
        header, payload = data.split("\n\n")
        for substring in ("sentry_timestamp=", "sentry_client="):
            assert substring in header

    def tearDown(self):
        self.server_socket.close()
