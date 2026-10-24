"""Isolate pinned BabelDOC paragraph mutations during opt-in concurrency."""
from contextlib import contextmanager
from copy import copy,deepcopy
from dataclasses import fields,is_dataclass
from threading import Lock

_STATE_LOCK=Lock()
_FLIGHTS={}


@contextmanager
def singleflight(engine,text):
    key=(id(engine),text)
    with _STATE_LOCK:
        lock,count=_FLIGHTS.get(key,(Lock(),0));_FLIGHTS[key]=(lock,count+1)
    try:
        with lock:yield
    finally:
        with _STATE_LOCK:
            lock,count=_FLIGHTS[key]
            if count==1:del _FLIGHTS[key]
            else:_FLIGHTS[key]=(lock,count-1)


def install(translator_class,engine_class):
    """Only install in a worker explicitly configured for parallel translation.

    Source list order/IDs stay fixed. Each task owns its paragraph and context;
    only completed paragraph fields are committed under a per-translator lock.
    Existing cache keys/validators remain unchanged. Duplicate simultaneous
    text uses the vendor cache after the first caller finishes, when enabled.
    """
    if getattr(translator_class,'_transformx_parallel_isolation',False):return
    original=translator_class.translate_paragraph
    cached=engine_class.translate

    def isolated(self,paragraph,page,*args,**kwargs):
        from transformation.llm.ollama_native import TRANSLATION_FAILED,record_progress
        with _STATE_LOCK:
            if not hasattr(self,'_transformx_commit_lock'):self._transformx_commit_lock=Lock()
        private=deepcopy(paragraph);private_page=copy(page);private_page.pdf_paragraph=[]
        kwargs=dict(kwargs)
        for key in ('title_paragraph','local_title_paragraph'):
            if key in kwargs:kwargs[key]=deepcopy(kwargs[key])
        try:
            result=original(self,private,private_page,*args,**kwargs)
            if private_page.pdf_paragraph:
                raise RuntimeError('Parallel translation produced page annotations; retry with one translation worker.')
            if getattr(private,'debug_id',None)!=getattr(paragraph,'debug_id',None):
                raise RuntimeError('Parallel translation changed a paragraph identifier.')
            names=[f.name for f in fields(private)] if is_dataclass(private) else vars(private)
            with self._transformx_commit_lock:
                for name in names:setattr(paragraph,name,getattr(private,name))
            return result
        except BaseException:
            TRANSLATION_FAILED.set();record_progress('parallel_translation_failed')
            raise

    def cache_serialized(self,text,*args,**kwargs):
        with singleflight(self,text):return cached(self,text,*args,**kwargs)

    translator_class.translate_paragraph=isolated
    engine_class.translate=cache_serialized
    translator_class._transformx_parallel_isolation=True
