"""One settings file for the whole system: transformx.toml.

Every part (site worker, engine service, start script, test harness) reads the same file. What
wins, from lowest to highest:

    built-in defaults  <  the site's worker connection file  <  transformx.toml  <  environment variables

so a Docker deployment can override one value with an environment variable without editing the
file. API keys never go in the file: it names key files instead.

The file is optional. Without one, everything works as before from environment variables.

    python -m transformation.settings show   [--file PATH]   settings in effect and where each came from
    python -m transformation.settings check  [--file PATH]   validate the file and test the model servers
    python -m transformation.settings env    [--file PATH]   shell exports, for tools/gpu/start_worker.sh
"""
import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DEFAULT_FILE = APP / 'configs' / 'transformx.toml'
FILE_ENV = 'TRANSFORMX_SETTINGS'


class SettingsError(ValueError):
    """The settings file has problems; str() lists every one of them."""


def _choice(*values):
    def check(value):
        if value not in values:
            raise ValueError('must be one of ' + ', '.join(f'"{v}"' for v in values))
        return value
    return check


def _positive(value):
    if value <= 0:
        raise ValueError('must be greater than 0')
    return value


def _not_negative(value):
    if value < 0:
        raise ValueError('must be 0 or more')
    return value


def _port(value):
    if not 0 < value < 65536:
        raise ValueError('must be a port number (1-65535)')
    return value


def _url(value):
    if value and not value.startswith(('http://', 'https://')):
        raise ValueError('must start with http:// or https://')
    return value


def _bool_env(value):
    return '1' if value else '0'


def _thinking_env(value):
    return {'auto': None, 'on': '1', 'off': '0'}[value]


# section -> key -> (type, check, where it goes, default shown by `show`, help)
# where: ('env', NAME[, convert]) sets an environment variable read by the engine;
#        ('profile', key) sets the engine profile; ('option', key[, convert]) sets an engine option.
SCHEMA = {
    'translation': {
        'label': (str, None, [], 'Standard (this server)', 'Name shown on the website for the default model'),
        'endpoint': (str, _url, [('env', 'TRANSFORMX_TRANSLATION_ENDPOINT')], 'local vLLM or Ollama',
                     'Base URL of the translation model server'),
        'model': (str, None, [('env', 'TRANSFORMX_TRANSLATION_MODEL')], 'translategemma:12b',
                  'Model name as the server knows it'),
        'key_file': (str, None, [('env', 'TRANSFORMX_TRANSLATION_API_KEY_FILE')], '',
                     'File holding the API key for the translation server'),
        'parallel_requests': (int, _positive, [('env', 'TRANSFORMX_TRANSLATION_WORKERS', str)], 'site config',
                              'Translation requests sent at the same time'),
        'requests_per_second': (int, _positive, [('env', 'TRANSFORMX_TRANSLATION_QPS', str)], 'site config',
                                'Upper limit on translation requests per second'),
    },
    'pii': {
        'endpoint': (str, _url, [('env', 'TRANSFORMX_PII_ENDPOINT')], 'local vLLM or Ollama',
                     'Base URL of the PII model server'),
        'model': (str, None, [('env', 'TRANSFORMX_PII_MODEL')], 'qwen3.5:9b', 'Model name as the server knows it'),
        'key_file': (str, None, [('env', 'TRANSFORMX_PII_API_KEY_FILE')], '',
                     'File holding the API key for the PII server'),
    },
    'models': {
        'remote_servers': (list, None, [('env', 'TRANSFORMX_OPENAI_COMPATIBLE', ','.join)], '[]',
                           'Model servers on other machines that may be used (base URLs)'),
        'allow_http_remote': (bool, None, [('env', 'TRANSFORMX_ALLOW_HTTP_REMOTE', _bool_env)], 'false',
                              'Allow plain http:// to listed remote servers (trusted internal network only)'),
        'remote_key_file': (str, None, [('env', 'TRANSFORMX_MODEL_API_KEY_FILE')], '',
                            'API key file for any other listed remote server'),
        'timeout_seconds': (int, _positive, [('env', 'TRANSFORMX_MODEL_TIMEOUT', str)], '600',
                            'Seconds to wait for one model answer'),
        'reasoning_effort': (str, None, [('env', 'TRANSFORMX_REASONING_EFFORT')], 'not sent',
                             'For reasoning models on remote servers, e.g. "minimal" or "low"'),
        'no_thinking_switch': (str, _choice('auto', 'on', 'off'),
                               [('env', 'TRANSFORMX_CHAT_TEMPLATE_KWARGS', _thinking_env)], 'auto',
                               'Send vLLM\'s "no thinking" switch (auto: local servers only)'),
    },
    'ocr': {
        'device': (str, None, [('profile', 'ocr_device')], 'site config', 'gpu:0, cpu, ...'),
        'keep_loaded': (bool, None, [('profile', 'persistent_ocr')], 'site config',
                        'Keep the OCR models loaded between jobs'),
        'reuse_results': (bool, None, [('option', 'ocr_cache_mode', lambda v: 'reuse' if v else 'fresh')],
                          'site config', 'Reuse OCR results of a page seen before'),
    },
    'output': {
        'tables': (str, None, [('option', 'table_strategy')], 'auto', 'How tables are rebuilt'),
        'page_mode': (str, _choice('document', 'page'), [('option', 'pdf_page_mode')], 'site config',
                      'Translate the document in one run or page by page'),
        'figures': (str, _choice('ocr-slots', 'babeldoc'), [('option', 'figure_engine')], 'site config',
                    'How text inside pictures is translated'),
        'job_timeout_seconds': (int, _positive, [('profile', 'timeout_seconds')], '3600',
                                'Longest time one job may run'),
    },
    'performance': {
        'figure_workers': (int, _positive, [('env', 'TRANSFORMX_FIGURE_WORKERS', str)], '3',
                           'Pictures translated at the same time (1 when models share the GPU)'),
        'shared_layout': (bool, None, [('env', 'TRANSFORMX_SHARED_LAYOUT', _bool_env)], 'true',
                          'One page-layout model shared by all jobs'),
        'preload': (bool, None, [('env', 'TRANSFORMX_PRELOAD', _bool_env)], 'true on GPU servers',
                    'Start job processes from pre-loaded parents'),
    },
    'service': {
        'host': (str, None, [('env', 'TRANSFORMX_SERVICE_HOST')], '127.0.0.1', 'Address the engine service listens on'),
        'port': (int, _port, [('env', 'TRANSFORMX_SERVICE_PORT', str)], '8080', 'Port of the engine service'),
        'key_file': (str, None, [('env', 'TRANSFORMX_SERVICE_API_KEY_FILE')], '',
                     'File holding the engine service API key (none: put a proxy with sign-in in front)'),
        'parallel_jobs': (int, _positive, [('env', 'TRANSFORMX_SERVICE_WORKERS', str)], '1',
                          'Jobs processed at the same time'),
        'keep_jobs_hours': (float, _not_negative, [('env', 'TRANSFORMX_SERVICE_RETENTION_HOURS', str)], '24',
                            'Delete finished jobs after this many hours'),
        'max_upload_mb': (int, _positive, [('env', 'TRANSFORMX_SERVICE_MAX_MB', str)], '50', 'Largest PDF accepted'),
        'jobs_folder': (str, None, [('env', 'TRANSFORMX_SERVICE_ROOT')], 'runs/engine-service', 'Folder for job files'),
    },
    'worker': {
        'connection_file': (str, None, [('env', 'TRANSFORMX_WORKER_CONFIG')], 'configs/gpu.worker.local.json',
                            'Connection file downloaded from the site\'s Workers panel'),
        'keep_runs_hours': (float, _not_negative, [('env', 'TRANSFORMX_RUN_RETENTION_HOURS', str)], '72',
                            'Delete local job folders after this many hours (0 keeps them)'),
        'min_free_gb': (float, _not_negative, [('env', 'TRANSFORMX_MIN_FREE_GB', str)], '5',
                        'Stop taking jobs while the job disk has less free space than this'),
    },
    'paths': {
        'engine_python': (str, None, [('env', 'TRANSFORMX_ENGINE_PYTHON')], 'found by the start script',
                          'Python of the document engine environment'),
        'babeldoc_python': (str, None, [('env', 'TRANSFORMX_BABELDOC_PYTHON')], 'found by the start script',
                            'Python of the BabelDOC environment'),
        'ocr_python': (str, None, [('profile', 'ocr_python')], 'site config', 'Python of the OCR environment'),
    },
}
# Advanced engine options, passed as they are: [engine_options] in the file.
RAW_SECTION = 'engine_options'
PATH_KEYS = {'key_file', 'remote_key_file', 'connection_file', 'jobs_folder', 'engine_python', 'babeldoc_python',
             'ocr_python'}


def find(path=None):
    """The settings file to use: the given path, $TRANSFORMX_SETTINGS, or configs/transformx.toml."""
    if path:
        return Path(path)
    if os.environ.get(FILE_ENV):
        return Path(os.environ[FILE_ENV])
    return DEFAULT_FILE if DEFAULT_FILE.exists() else None


def _type_name(kind):
    return {str: 'text', int: 'a whole number', float: 'a number', bool: 'true or false', list: 'a list'}[kind]


# Other translation models users may pick on the website or per engine-service job:
# [translation_models.<id>] tables. The [translation] section stays the default ("default").
CHOICES_SECTION = 'translation_models'
CHOICE_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
CHOICE_KEYS = {
    'label': (str, None, 'Name shown on the website'),
    'endpoint': (str, _url, 'Base URL of the OpenAI-compatible server'),
    'model': (str, None, 'Model name as that server knows it'),
    'key_file': (str, None, 'File holding the API key for that server'),
    'requests_per_second': (int, _positive, 'Upper limit on requests per second'),
}
MAX_CHOICES = 8


def _validate_choices(values, clean):
    problems = []
    if not isinstance(values, dict):
        return [f'[{CHOICES_SECTION}] must hold [{CHOICES_SECTION}.<name>] sections']
    if len(values) > MAX_CHOICES:
        problems.append(f'[{CHOICES_SECTION}]: at most {MAX_CHOICES} models')
    for choice, table in values.items():
        name = f'{CHOICES_SECTION}.{choice}'
        if not CHOICE_ID.match(choice) or choice == 'default':
            problems.append(f'[{name}]: the name must be lower-case letters, digits, - or _ (up to 32), not "default"')
            continue
        if not isinstance(table, dict):
            problems.append(f'[{name}] must be a section')
            continue
        entry, ok = {}, True
        for key, value in table.items():
            if key not in CHOICE_KEYS:
                problems.append(f'{name}.{key}: unknown setting (known: {", ".join(CHOICE_KEYS)})')
                ok = False
                continue
            kind, check, _ = CHOICE_KEYS[key]
            if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
                problems.append(f'{name}.{key}: must be {_type_name(kind)}, not {value!r}')
                ok = False
                continue
            if check:
                try:
                    value = check(value)
                except ValueError as error:
                    problems.append(f'{name}.{key}: {error}')
                    ok = False
                    continue
            entry[key] = value
        for required in ('endpoint', 'model'):
            if not entry.get(required) and ok:
                problems.append(f'{name}: {required} is required')
                ok = False
        if ok:
            clean.setdefault(CHOICES_SECTION, {})[choice] = entry
    return problems


def validate(data):
    """Check parsed TOML against SCHEMA; return (clean settings, list of problems)."""
    clean, problems = {}, []
    for section, values in data.items():
        if section == CHOICES_SECTION:
            problems += _validate_choices(values, clean)
            continue
        if section == RAW_SECTION:
            if not isinstance(values, dict):
                problems.append(f'[{RAW_SECTION}] must be a table of option = value')
                continue
            for key, value in values.items():
                if not isinstance(value, (str, int, float, bool)):
                    problems.append(f'{RAW_SECTION}.{key}: must be text, a number or true/false')
                    continue
                clean.setdefault(RAW_SECTION, {})[key] = value
            continue
        if section not in SCHEMA:
            problems.append(f'[{section}]: unknown section (known: {", ".join(list(SCHEMA) + [CHOICES_SECTION, RAW_SECTION])})')
            continue
        if not isinstance(values, dict):
            problems.append(f'[{section}] must be a section, not a single value')
            continue
        for key, value in values.items():
            spec = SCHEMA[section].get(key)
            name = f'{section}.{key}'
            if spec is None:
                problems.append(f'{name}: unknown setting (known: {", ".join(SCHEMA[section])})')
                continue
            kind, check = spec[0], spec[1]
            if kind is float and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
                problems.append(f'{name}: must be {_type_name(kind)}, not {value!r}')
                continue
            if kind is list and not all(isinstance(item, str) for item in value):
                problems.append(f'{name}: must be a list of text values')
                continue
            if kind is list and check is None and section == 'models':
                bad = [item for item in value if not item.startswith(('http://', 'https://'))]
                if bad:
                    problems.append(f'{name}: {bad[0]!r} must start with http:// or https://')
                    continue
            if check:
                try:
                    value = check(value)
                except ValueError as error:
                    problems.append(f'{name}: {error}')
                    continue
            clean.setdefault(section, {})[key] = value
    return clean, problems


def read(path):
    """Parse and validate a settings file. Raises SettingsError listing every problem."""
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        raise SettingsError('Reading transformx.toml needs Python 3.11 or newer.')
    path = Path(path)
    if not path.is_file():
        raise SettingsError(f'Settings file not found: {path}')
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f'{path}: not valid TOML ({error})')
    clean, problems = validate(data)
    if problems:
        raise SettingsError(f'{path}:\n  ' + '\n  '.join(problems))
    # Relative file and folder paths are relative to the application folder, like configs/...
    for section, values in clean.items():
        for key, value in values.items():
            if section != RAW_SECTION and key in PATH_KEYS and value and not Path(value).is_absolute():
                values[key] = str(APP / value)
    for entry in clean.get(CHOICES_SECTION, {}).values():
        if entry.get('key_file') and not Path(entry['key_file']).is_absolute():
            entry['key_file'] = str(APP / entry['key_file'])
    return clean


def _targets(clean):
    """Yield (section, key, value, kind, name, converted value) for every setting in the file."""
    for section, values in clean.items():
        if section == CHOICES_SECTION:
            continue
        if section == RAW_SECTION:
            for key, value in values.items():
                yield section, key, value, 'option', key, value
            continue
        for key, value in values.items():
            for target in SCHEMA[section][key][2]:
                convert = target[2] if len(target) > 2 else (lambda v: v)
                yield section, key, value, target[0], target[1], convert(value)


def environment(clean):
    """Environment variables the file sets: {NAME: value or None (leave unset)}."""
    return {name: converted for _, _, _, kind, name, converted in _targets(clean) if kind == 'env'}


def apply(profile=None, path=None, environ=None):
    """Load the settings file (if any) into this process: environment variables the file sets are
    exported unless already set (the environment wins), and profile values and engine options are
    written into `profile`. Returns the file used, or None."""
    environ = os.environ if environ is None else environ
    path = find(path)
    if path is None:
        return None
    clean = read(path)
    for _, _, _, kind, name, converted in _targets(clean):
        if kind == 'env':
            if converted is not None and name not in environ:
                environ[name] = converted
        elif profile is not None and kind == 'profile':
            profile[name] = converted
        elif profile is not None and kind == 'option':
            profile.setdefault('engine_options', {})[name] = converted
    environ.setdefault(FILE_ENV, str(Path(path).resolve()))
    return path


def report(path=None, environ=None):
    """Rows of (setting, value, source) for every known setting: environment, file or default."""
    environ = os.environ if environ is None else environ
    path = find(path)
    clean = read(path) if path else {}
    rows = []
    for section, keys in SCHEMA.items():
        for key, spec in keys.items():
            name = f'{section}.{key}'
            env_names = [t[1] for t in spec[2] if t[0] == 'env']
            in_env = next((n for n in env_names if n in environ and (path is None or
                           environ.get(FILE_ENV) != str(Path(path).resolve()) or
                           environment(clean).get(n) != environ[n])), None)
            if in_env:
                value, source = environ[in_env], f'environment ({in_env})'
            elif key in clean.get(section, {}):
                value, source = clean[section][key], 'file'
            else:
                value, source = spec[3], 'default'
            if key.endswith('key_file') and value and source != 'default':
                value = f'{value} ({"readable" if Path(str(value)).is_file() else "MISSING"})'
            rows.append((name, value, source))
    for choice, entry in clean.get(CHOICES_SECTION, {}).items():
        for key, value in entry.items():
            if key == 'key_file':
                value = f'{value} ({"readable" if Path(value).is_file() else "MISSING"})'
            rows.append((f'{CHOICES_SECTION}.{choice}.{key}', value, 'file'))
    for key, value in clean.get(RAW_SECTION, {}).items():
        rows.append((f'{RAW_SECTION}.{key}', value, 'file'))
    for name in ('TRANSFORMX_TRANSLATION_API_KEY', 'TRANSFORMX_PII_API_KEY', 'TRANSFORMX_MODEL_API_KEY',
                 'TRANSFORMX_SERVICE_API_KEY'):
        if environ.get(name):
            rows.append((name, '(set, hidden)', 'environment'))
    return path, rows


def _value_text(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, list):
        return json.dumps(value)
    return str(value)


def show(path=None):
    used, rows = report(path)
    print(f'Settings file: {used or "none (defaults and environment only)"}')
    width = max(len(name) for name, _, _ in rows)
    for name, value, source in rows:
        print(f'  {name:<{width}}  {_value_text(value):<40}  {source}')


def check(path=None, probe=True):
    """Validate the file, the key files and the model server rules; optionally ask each model server
    for its models. Returns a list of problems (empty: all good)."""
    problems = []
    used = find(path)
    try:
        clean = read(used) if used else {}
    except SettingsError as error:
        lines = str(error).splitlines()
        return [line.strip() for line in lines[1:]] if len(lines) > 1 else lines
    environ = dict(os.environ)
    apply(path=used, environ=environ) if used else None
    for section, key in (('translation', 'key_file'), ('pii', 'key_file'), ('models', 'remote_key_file'),
                         ('service', 'key_file')):
        value = clean.get(section, {}).get(key)
        if value and not Path(value).is_file():
            problems.append(f'{section}.{key}: file not found: {value}')
    for choice in translation_choices(used, environ)[1:]:
        if choice.get('key_file') and not Path(choice['key_file']).is_file():
            problems.append(f'{CHOICES_SECTION}.{choice["id"]}.key_file: file not found: {choice["key_file"]}')
    if not probe:
        return problems
    saved = dict(os.environ)
    try:
        os.environ.clear(); os.environ.update(environ)
        from transformation.llm import endpoints
        for role in ('translation', 'pii'):
            endpoint = environ.get(f'TRANSFORMX_{role.upper()}_ENDPOINT')
            model = environ.get(f'TRANSFORMX_{role.upper()}_MODEL')
            if not endpoint:
                continue
            try:
                endpoints.check(endpoint)
            except ValueError as error:
                problems.append(f'{role}.endpoint: {error}')
                continue
            problem = _probe(endpoints, endpoint, model)
            if problem:
                problems.append(f'{role}: {problem}')
        for choice in translation_choices(used, environ)[1:]:
            os.environ.clear(); os.environ.update(choice_environment(choice, environ))
            problem = _probe(endpoints, choice['endpoint'], choice['model'])
            if problem:
                problems.append(f'{CHOICES_SECTION}.{choice["id"]}: {problem}')
    finally:
        os.environ.clear(); os.environ.update(saved)
    return problems


def translation_choices(path=None, environ=None):
    """Translation models a job may choose: 'default' (the [translation] settings) first, then each
    [translation_models.<id>]. Entries hold id, label, and for named models endpoint, model, key_file
    and requests_per_second. Keys themselves are never read here."""
    environ = os.environ if environ is None else environ
    used = find(path)
    clean = read(used) if used else {}
    label = (clean.get('translation', {}).get('label') or environ.get('TRANSFORMX_TRANSLATION_LABEL')
             or 'Standard (this server)')
    choices = [{'id': 'default', 'label': label[:80]}]
    for choice, entry in clean.get(CHOICES_SECTION, {}).items():
        choices.append({'id': choice, 'label': (entry.get('label') or entry['model'])[:80],
                        'endpoint': entry['endpoint'], 'model': entry['model'],
                        'key_file': entry.get('key_file', ''),
                        'requests_per_second': entry.get('requests_per_second')})
    return choices


def choice_environment(choice, environ=None):
    """The environment for a job that uses a named translation model: that server, model and key
    file only. The default translation key is removed, so it can never be sent to that server."""
    env = dict(os.environ if environ is None else environ)
    if choice['id'] == 'default':
        return env
    env['TRANSFORMX_TRANSLATION_ENDPOINT'] = choice['endpoint']
    env['TRANSFORMX_TRANSLATION_MODEL'] = choice['model']
    env.pop('TRANSFORMX_TRANSLATION_API_KEY', None)
    env.pop('TRANSFORMX_TRANSLATION_API_KEY_FILE', None)
    if choice.get('key_file'):
        env['TRANSFORMX_TRANSLATION_API_KEY_FILE'] = choice['key_file']
    if choice.get('requests_per_second'):
        env['TRANSFORMX_TRANSLATION_QPS'] = str(choice['requests_per_second'])
    # Naming a server in the settings file is the operator's decision to use it: it is listed as an
    # OpenAI-compatible server for this job.
    from transformation.llm.endpoints import root
    listed = [s.strip() for s in env.get('TRANSFORMX_OPENAI_COMPATIBLE', '').split(',') if s.strip()]
    if root(choice['endpoint']) not in {root(s) for s in listed}:
        listed.append(choice['endpoint'])
    env['TRANSFORMX_OPENAI_COMPATIBLE'] = ','.join(listed)
    return env


def choice_profile(choice, profile):
    """The engine profile for a job that uses `choice` (a copy; the worker's profile is unchanged)."""
    profile = json.loads(json.dumps(profile))
    if choice['id'] == 'default':
        return profile
    profile['translation_endpoint'] = choice['endpoint']
    profile['translation_model'] = choice['model']
    if choice.get('requests_per_second'):
        profile.setdefault('engine_options', {})['translation_qps'] = choice['requests_per_second']
    return profile


def _probe(endpoints, endpoint, model):
    """Ask an OpenAI-compatible server which models it serves."""
    import urllib.error
    import urllib.request
    url = endpoints.root(endpoint).rstrip('/') + '/v1/models'
    request = urllib.request.Request(url, headers=endpoints.headers(endpoint))
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            served = [item.get('id') for item in json.loads(response.read()).get('data', [])]
    except urllib.error.HTTPError as error:
        return {401: 'the server refused the API key (401)', 403: 'the API key has no access (403)',
                404: f'no model list at {url} (404): is the endpoint right?'}.get(error.code,
                f'the server answered {error.code}')
    except Exception as error:
        return f'no server answers at {endpoint} ({type(error).__name__})'
    if model and served and model not in served:
        return f'model "{model}" is not served there (served: {", ".join(map(str, served[:8]))})'
    return None


def shell_exports(path=None):
    """`export NAME=value` lines for the variables the file sets, skipping ones already set."""
    used = find(path)
    if not used:
        return ''
    lines = []
    for name, value in environment(read(used)).items():
        if value is not None and name not in os.environ:
            lines.append(f'export {name}={shlex.quote(value)}')
    lines.append(f'export {FILE_ENV}={shlex.quote(str(Path(used).resolve()))}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m transformation.settings', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['show', 'check', 'env'])
    parser.add_argument('--file', help='settings file (default: $TRANSFORMX_SETTINGS or configs/transformx.toml)')
    parser.add_argument('--no-probe', action='store_true', help='check: do not contact the model servers')
    args = parser.parse_args(argv)
    try:
        if args.command == 'show':
            show(args.file)
        elif args.command == 'env':
            print(shell_exports(args.file))
        else:
            problems = check(args.file, probe=not args.no_probe)
            for problem in problems:
                print('PROBLEM ' + problem)
            print('Settings OK.' if not problems else f'{len(problems)} problem(s).')
            return 1 if problems else 0
    except SettingsError as error:
        print(f'Settings problem: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
