"""Content-free timings over the pinned vendor progress stages."""
import time


def install(monitor=None):
    if monitor is None:
        from babeldoc.progress_monitor import ProgressMonitor
        monitor=ProgressMonitor
    if getattr(monitor,'_transformx_timing_installed',False):return
    start,done=monitor.stage_start,monitor.stage_done
    from transformation.llm.ollama_native import record_progress
    def stage_start(self,name,total):
        stage=start(self,name,total)
        times=getattr(self,'_transformx_stage_times',None)
        if times is None:times={};self._transformx_stage_times=times
        times[id(stage)]=time.monotonic()
        record_progress('pdf_stage_start',stage=name)
        return stage
    def stage_done(self,stage):
        try:return done(self,stage)
        finally:
            started=getattr(self,'_transformx_stage_times',{}).pop(id(stage),None)
            if started is not None:
                record_progress('pdf_stage_end',stage=stage.name,seconds=round(time.monotonic()-started,6))
    monitor.stage_start=stage_start;monitor.stage_done=stage_done
    monitor._transformx_timing_installed=True


def install_rate_timing(limiter=None):
    if limiter is None:
        from babeldoc.translator.translator import RateLimiter
        limiter=RateLimiter
    if getattr(limiter,'_transformx_timing_installed',False):return
    original=limiter.wait
    def wait(self,*args,**kwargs):
        started=time.monotonic()
        try:return original(self,*args,**kwargs)
        finally:
            elapsed=time.monotonic()-started
            if elapsed>=.001:
                from transformation.llm.ollama_native import record_progress
                record_progress('pdf_rate_limit_wait',seconds=round(elapsed,6))
    limiter.wait=wait
    limiter._transformx_timing_installed=True
