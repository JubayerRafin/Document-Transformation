"""Where the engine may send model requests, and how.

By default only model servers on this machine are used (localhost, 127.0.0.1, ::1): Ollama, or
OpenAI-compatible servers such as vLLM. A server on another machine is used only when it is listed
explicitly as OpenAI-compatible, so documents are never sent anywhere by accident.

Settings, all from the environment (never from job options, so they are not saved with runs):

TRANSFORMX_OPENAI_COMPATIBLE   comma-separated OpenAI-compatible servers, e.g.
                               "http://127.0.0.1:8010,https://llm.internal.example/team/v1".
                               A trailing /v1 is optional. Remote servers must be listed here.
TRANSFORMX_ALLOW_HTTP_REMOTE   "1" allows plain http:// to remote servers (a trusted internal
                               network only); otherwise remote servers must use https://.
TRANSFORMX_TRANSLATION_API_KEY / TRANSFORMX_TRANSLATION_API_KEY_FILE
TRANSFORMX_PII_API_KEY / TRANSFORMX_PII_API_KEY_FILE
                               bearer key sent to the server configured as
                               TRANSFORMX_TRANSLATION_ENDPOINT / TRANSFORMX_PII_ENDPOINT.
TRANSFORMX_MODEL_API_KEY / TRANSFORMX_MODEL_API_KEY_FILE
                               bearer key for any other listed remote server.
TRANSFORMX_MODEL_TIMEOUT       seconds to wait for one model answer (default 600).
TRANSFORMX_CHAT_TEMPLATE_KWARGS
                               "1" or "0": send vLLM's chat_template_kwargs (turns off "thinking").
                               Default: sent to local servers only, since other providers may
                               reject unknown request fields.

Keys are read when a request is made and are never logged or written to run folders.
"""
import os
from pathlib import Path
from urllib.parse import urlparse

LOOPBACK = {'localhost', '127.0.0.1', '::1'}
OPENAI_SERVERS_ENV = 'TRANSFORMX_OPENAI_COMPATIBLE'


def root(endpoint):
    """scheme://host[:port][/path] without a trailing /v1 or slash."""
    parsed = urlparse((endpoint or '').strip())
    path = parsed.path.rstrip('/')
    if path.endswith('/v1'):
        path = path[:-3]
    return f'{parsed.scheme}://{parsed.netloc}{path}'


def host_base(endpoint):
    """scheme://host[:port], the unit a GPU-sharing model server is known by."""
    parsed = urlparse(endpoint)
    return f'{parsed.scheme}://{parsed.netloc}'


def is_loopback(endpoint):
    return urlparse(endpoint).hostname in LOOPBACK


def listed():
    return {root(item) for item in os.environ.get(OPENAI_SERVERS_ENV, '').split(',') if item.strip()}


def kind(endpoint):
    """'openai' for a listed OpenAI-compatible server, else 'ollama' (local Ollama)."""
    return 'openai' if root(endpoint) in listed() or host_base(endpoint) in listed() else 'ollama'


def check(endpoint):
    """Raise ValueError unless model requests may be sent to endpoint."""
    parsed = urlparse((endpoint or '').strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('A model endpoint must be an http:// or https:// address.')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('A model endpoint must not contain a user name, password, query or fragment; '
                         'set API keys in the environment instead.')
    if parsed.hostname in LOOPBACK:
        return
    if kind(endpoint) != 'openai':
        raise ValueError(f'Model server {host_base(endpoint)} is not on this machine. To use a remote '
                         f'OpenAI-compatible server, list it in {OPENAI_SERVERS_ENV}.')
    if parsed.scheme != 'https' and os.environ.get('TRANSFORMX_ALLOW_HTTP_REMOTE') != '1':
        raise ValueError(f'Remote model server {host_base(endpoint)} must use https:// '
                         '(set TRANSFORMX_ALLOW_HTTP_REMOTE=1 only on a trusted internal network).')


def _secret(name):
    value = os.environ.get(name, '').strip()
    if value:
        return value
    path = os.environ.get(name + '_FILE', '').strip()
    if path:
        try:
            return Path(path).read_text(encoding='utf-8').strip() or None
        except OSError as exc:
            raise ValueError(f'Cannot read the key file named by {name}_FILE.') from exc
    return None


def api_key(endpoint):
    """The bearer key for endpoint, or None."""
    target = root(endpoint)
    for role in ('TRANSLATION', 'PII'):
        configured = os.environ.get(f'TRANSFORMX_{role}_ENDPOINT', '').strip()
        if configured and root(configured) == target:
            key = _secret(f'TRANSFORMX_{role}_API_KEY')
            if key:
                return key
    if not is_loopback(endpoint) and kind(endpoint) == 'openai':
        return _secret('TRANSFORMX_MODEL_API_KEY')
    return None


def headers(endpoint):
    result = {'Content-Type': 'application/json'}
    key = api_key(endpoint)
    if key:
        result['Authorization'] = 'Bearer ' + key
    return result


def url(endpoint, path):
    """Full URL of an OpenAI-style path ('/v1/chat/completions', '/v1/models') on endpoint."""
    return root(endpoint) + path


def timeout():
    try:
        value = float(os.environ.get('TRANSFORMX_MODEL_TIMEOUT', '600'))
    except ValueError:
        value = 600.0
    return value if value > 0 else 600.0


def chat_template_kwargs(endpoint):
    setting = os.environ.get('TRANSFORMX_CHAT_TEMPLATE_KWARGS')
    if setting in {'0', '1'}:
        return setting == '1'
    return is_loopback(endpoint)


def is_translategemma(model):
    """TranslateGemma needs its own prompt format, whatever name the server gives it
    ('translategemma:12b' locally, 'google/translategemma-12b-it' elsewhere)."""
    return 'translategemma' in (model or '').lower()
