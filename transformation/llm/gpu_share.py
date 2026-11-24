"""One GPU shared by model servers that do not fit on it together.

On a GPU too small for all models at once (a 24 GB card: PII model 15.5 GB, translation model
10-15 GB), tools/gpu/start_worker.sh starts each vLLM server with sleep mode and lists their base
URLs in TRANSFORMX_GPU_SWAP. Before a model call, the server about to be called gets the GPU:
  a listed vLLM server - Ollama's loaded models are unloaded, the other listed servers are put to
                         sleep (weights move to CPU memory; about 1 s, measured 2026-11-16 on an
                         RTX 4090), then this one is woken (about 1 s);
  any other server     - (Ollama) every listed vLLM server is put to sleep; Ollama then loads its
                         model as usual.
A worker runs one job at a time and a job's stages run one after another, so two servers are never
needed at the same moment. Without the variable (a GPU that holds everything) nothing happens.
"""
import contextlib
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

SWAP_ENV = 'TRANSFORMX_GPU_SWAP'
OLLAMA_ENV = 'TRANSFORMX_GPU_SWAP_OLLAMA'
LOCK_PATH = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'transformx-gpu-swap.lock')
# Sleep level each server was last put to sleep at, shared by every process on the machine. A server
# put to sleep at level 2 has no weights; woken like a level-1 server (without reloading them), it
# answers from empty memory: blank text until the token limit (seen 2026-11-16 when two processes
# had different TRANSFORMX_GPU_SWAP lists). The recorded level wins over the configured one.
LEVELS_PATH = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'transformx-gpu-swap-levels.json')


def _base(url):
    parsed = urlparse(url)
    return f'{parsed.scheme}://{parsed.netloc}'


def _request(url, data=None, method='GET', timeout=120):
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(url, body, {'Content-Type': 'application/json'}, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw.strip() else {}


def _sleeping(server):
    return bool(_request(server + '/is_sleeping', timeout=10).get('is_sleeping'))


def _ollama_loaded(ollama):
    return [m['name'] for m in _request(ollama + '/api/ps', timeout=10).get('models', [])]


@contextlib.contextmanager
def _switch_lock():
    """The engine and the BabelDOC worker are separate processes: switch one at a time."""
    try:
        import fcntl
    except ImportError:  # not a Linux GPU server; nothing else switches here
        yield; return
    with open(LOCK_PATH, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try: yield
        finally: fcntl.flock(lock, fcntl.LOCK_UN)


def _entries():
    """[(base URL, sleep level)] from TRANSFORMX_GPU_SWAP: 'http://127.0.0.1:8000,http://127.0.0.1:8010#2'.
    Level 1 parks the weights in pinned CPU memory (wake about 1 s) and vLLM keeps that buffer
    (about 14 GiB) allocated afterwards. Two such servers exceeded a 41 GB pod's memory limit and
    the kernel killed a server (2026-11-16), so a second server sleeps at level 2: weights are
    discarded and reloaded from disk on wake (about 10 s for TranslateGemma). Level 2 suits a server
    without a speculative draft model (vLLM's reload does not restore draft weights)."""
    return [(base, level) for base, level, _ in _members()]


def _members():
    """[(base URL, sleep level, group)]. Servers joined by '+' are one group that fits on the GPU
    together and stays awake together ('http://127.0.0.1:8000+http://127.0.0.1:8010,http://127.0.0.1:8011'
    on an L40S: the PII model and TranslateGemma 12B share it, TranslateGemma 27B needs it alone)."""
    members = []
    for group, part in enumerate(os.environ.get(SWAP_ENV, '').split(',')):
        for item in part.split('+'):
            item = item.strip()
            if not item: continue
            url, _, level = item.partition('#')
            members.append((_base(url), 2 if level.strip() == '2' else 1, group))
    return members


def shared_servers():
    return [base for base, _ in _entries()]


def _recorded():
    try:
        with open(LEVELS_PATH, encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(server, level):
    data = _recorded()
    if level is None: data.pop(server, None)
    else: data[server] = level
    try:
        with open(LEVELS_PATH + '.tmp', 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(LEVELS_PATH + '.tmp', LEVELS_PATH)
    except OSError:
        pass  # the configured level still applies


def _sleep(server, levels):
    level = max(levels.get(server, 1), int(_recorded().get(server, 1)))
    _request(server + f'/sleep?level={level}', method='POST')
    # Only a server that was awake reaches this level now; one already asleep keeps its record.
    if level == 2 or server not in _recorded(): _record(server, level)


def _wake(server, levels):
    if max(levels.get(server, 1), int(_recorded().get(server, 1))) == 2:
        _request(server + '/wake_up?tags=weights', method='POST')
        _request(server + '/collective_rpc', {'method': 'reload_weights'}, method='POST', timeout=600)
        _request(server + '/wake_up?tags=kv_cache', method='POST')
    else:
        _request(server + '/wake_up', method='POST')
    _record(server, None)


def ensure(endpoint):
    """Give the GPU to the model server at endpoint before calling it."""
    levels = dict(_entries())
    servers = list(levels)
    if not servers: return
    target = _base(endpoint)
    ollama = _base(os.environ.get(OLLAMA_ENV, 'http://127.0.0.1:11434'))
    group = {base: g for base, _, g in _members()}
    # Servers of the target's own group may stay awake beside it.
    others = [s for s in servers if group.get(s) != group.get(target)]
    if target not in servers:
        if all(_sleeping(s) for s in servers): return
        with _switch_lock():
            for server in servers:
                if not _sleeping(server): _sleep(server, levels)
        return
    if not _sleeping(target) and all(_sleeping(s) for s in others) and not _ollama_loaded(ollama): return
    with _switch_lock():
        loaded = _ollama_loaded(ollama)
        for name in loaded:
            _request(ollama + '/api/generate', {'model': name, 'keep_alive': 0}, method='POST', timeout=60)
        deadline = time.monotonic() + 30
        while loaded and time.monotonic() < deadline:
            time.sleep(.2); loaded = _ollama_loaded(ollama)
        if loaded: raise RuntimeError('Ollama did not release the GPU for the next model server.')
        # Asked even when a server reports sleeping: a switch interrupted mid-wake (a stopped job)
        # leaves weights on the GPU while /is_sleeping still says true (seen 2026-11-16: 15 GB held,
        # the next job's OCR and model wake ran out of memory). vLLM frees whatever is still awake
        # and ignores the request when the server is fully asleep.
        for server in others:
            _sleep(server, levels)
        if _sleeping(target):
            try:
                _wake(target, levels)
            except urllib.error.HTTPError as exc:
                raise RuntimeError('A model server could not wake up on the GPU.') from exc
