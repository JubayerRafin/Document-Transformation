"""One loopback HTTP connection per worker thread; never replay failed requests."""
import http.client
import json
from io import BytesIO
from threading import local
from urllib.error import HTTPError
from urllib.parse import urlparse

_state = local()


def close():
    connection = getattr(_state, 'connection', None)
    if connection is not None:
        connection.close()
    _state.connection = None
    _state.identity = None


def post_json(url, payload, timeout=600):
    parsed = urlparse(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Only a loopback model endpoint is permitted.')
    identity = (parsed.scheme, parsed.hostname, parsed.port, timeout)
    if getattr(_state, 'identity', None) != identity or getattr(_state, 'connection', None) is None:
        close()
        factory = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        _state.connection = factory(parsed.hostname, parsed.port, timeout=timeout)
        _state.identity = identity
    connection = _state.connection
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('POST', path, body=json.dumps(payload).encode('utf-8'),
                           headers={'Content-Type': 'application/json', 'Connection': 'keep-alive'})
        response = connection.getresponse()
        status, reason, headers = response.status, response.reason, response.headers
        body = response.read()  # Fully consume the response before reusing the socket.
        if response.will_close:
            close()
        if status >= 400:
            raise HTTPError(url, status, reason, headers, BytesIO(body))
        return json.loads(body)
    except Exception:
        # A broken/expired tunnel must surface as an error, not silently duplicate
        # model work. The caller's existing retry policy owns any next attempt.
        close()
        raise
