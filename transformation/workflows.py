"""Independent workflows over the same extraction, IR and reconstruction code."""

def run_pipeline(*args,**options):
    # Keep the connected UI from importing extraction/model dependencies.
    from transformation.pipeline import run_pipeline as engine
    yield from engine(*args,**options)

DEFAULT_TRANSLATION_MODEL='translategemma:12b'
DEFAULT_PII_MODEL='qwen3.5:9b'


def check_models(workflow,translation_model=DEFAULT_TRANSLATION_MODEL,pii_model=DEFAULT_PII_MODEL,
                 translation_endpoint='http://127.0.0.1:11434/v1',pii_endpoint='http://127.0.0.1:11434/v1',*,visual_analysis=False):
    import json,urllib.request
    from urllib.parse import urlparse
    choices=[]
    if workflow!='PII only': choices.append(('Translation',translation_model,translation_endpoint))
    if workflow!='Translation only': choices.append(('PII',pii_model,pii_endpoint))
    if visual_analysis:
        choices.append(('Visual document understanding',translation_model,translation_endpoint)
                       if workflow!='PII only' else ('Visual document understanding',pii_model,pii_endpoint))
    seen={}
    for role,model,endpoint in choices:
        parsed=urlparse(endpoint)
        if parsed.hostname not in {'localhost','127.0.0.1','::1'} or parsed.scheme not in {'http','https'}:
            raise ValueError('Use a loopback Ollama endpoint or SSH tunnel.')
        from transformation.llm.ollama_native import server_kind
        base=f'{parsed.scheme}://{parsed.netloc}';kind=server_kind(base)
        url=base+('/v1/models' if kind=='openai' else '/api/tags')
        if url not in seen:
            try:
                with urllib.request.urlopen(url,timeout=5) as response:
                    listing=json.load(response)
                    seen[url]={m['id'] for m in listing['data']} if kind=='openai' else {m['name'] for m in listing['models']}
            except (OSError,ValueError,KeyError,TypeError) as exc:
                raise ValueError('Cannot reach the '+role+' model server; check its endpoint or tunnel.') from exc
        if model not in seen[url] and model+':latest' not in seen[url]:
            if kind=='openai':raise ValueError(f'{role} model {model} is not served at {base}.')
            raise ValueError(f'{role} model is not installed yet: {model}. Complete ollama pull {model} on that server first.')


def run_workflow(data,filename,workflow='Translation only',**options):
    modes={'Translation only':(True,False),'PII only':(False,True),'Translation + PII':(True,True)}
    if workflow not in modes: raise ValueError('Unknown document workflow.')
    execution_remote=options.pop('execution_remote',None)
    if execution_remote:
        from transformation.remote_pipeline import run_remote_pipeline
        yield from run_remote_pipeline(data,filename,workflow,execution_remote,**options)
        return
    options.pop('translate',None); options.pop('redact',None)
    options.setdefault('translation_model',DEFAULT_TRANSLATION_MODEL)
    options.setdefault('pii_model',DEFAULT_PII_MODEL)
    translate,redact=modes[workflow]
    yield from run_pipeline(data,filename,translate=translate,redact=redact,**options)


def run_translation(data,filename,**options):
    yield from run_workflow(data,filename,'Translation only',**options)


def run_pii(data,filename,**options):
    yield from run_workflow(data,filename,'PII only',**options)
