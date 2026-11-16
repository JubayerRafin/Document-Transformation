"""Run BabelDOC's page-layout model on the GPU when the server asks for it.

BabelDOC builds its ONNX session with CPU providers only. On a GPU server that takes 6-15 s per
document (measured 2026-11-16, RTX 4090 pod, busy shared CPU). With TRANSFORMX_LAYOUT_DEVICE=cuda
and the GPU build of ONNX Runtime installed (tools/gpu/setup_full_pipeline.sh), the same model runs
on CUDA, with the CPU kept as fallback. Without the variable, or without CUDA, nothing changes.
When the GPU has no room for the session (a model server or an earlier job stage holds the memory:
seen 2026-11-16 on combined PII + translation jobs on a 24 GB card), the session is built on the CPU
instead of failing the job.
"""
import os
import sys

CUDA = 'CUDAExecutionProvider'


def install(module=None):
    """Returns the providers layout sessions will use, or None when unchanged."""
    if os.environ.get('TRANSFORMX_LAYOUT_DEVICE', '').lower() not in {'cuda', 'gpu'}:
        return None
    if module is None:
        from babeldoc.docvision import doclayout as module
    runtime = module.onnxruntime
    if getattr(runtime, '_transformx_layout_device', None):
        return runtime._transformx_layout_device
    if CUDA not in runtime.get_available_providers():
        return None
    # The GPU build finds the CUDA/cuDNN libraries installed as pip packages only when asked to load
    # them first; otherwise the CUDA provider fails to load and ONNX Runtime silently uses the CPU.
    if hasattr(runtime, 'preload_dlls'):
        runtime.preload_dlls()
    # cuDNN's exhaustive convolution search costs seconds on the first run in each new process
    # (BabelDOC starts one per job); the heuristic choice is the usual fast path.
    providers = [(CUDA, {'cudnn_conv_algo_search': 'HEURISTIC'}), 'CPUExecutionProvider']

    class Runtime:
        _transformx_layout_device = providers

        def __getattr__(self, name): return getattr(runtime, name)

        def InferenceSession(self, *args, **kwargs):
            kwargs['providers'] = list(providers)
            try:
                return runtime.InferenceSession(*args, **kwargs)
            except Exception as exc:  # ONNX Runtime raises its own RuntimeException on GPU OOM
                print(f'Layout model on CPU: the GPU session could not start ({str(exc)[:200]})',
                      file=sys.stderr, flush=True)
                kwargs['providers'] = ['CPUExecutionProvider']
                return runtime.InferenceSession(*args, **kwargs)

    module.onnxruntime = Runtime()
    return providers
