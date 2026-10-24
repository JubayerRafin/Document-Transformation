"""Reduce idle CPU contention without changing layout threads or model math."""
import os


def install(module=None):
    if os.environ.get('TRANSFORMX_LAYOUT_IDLE_SPINNING') != '0':
        return False
    if module is None:
        from babeldoc.docvision import doclayout as module
    original=module.onnxruntime
    if getattr(original,'_transformx_no_spin',False):return True

    class Runtime:
        _transformx_no_spin=True

        def __getattr__(self,name):return getattr(original,name)

        def InferenceSession(self,*args,**kwargs):
            # Respect a caller's explicitly supplied options.
            if len(args)<2 and kwargs.get('sess_options') is None:
                options=original.SessionOptions()
                options.add_session_config_entry('session.intra_op.allow_spinning','0')
                options.add_session_config_entry('session.inter_op.allow_spinning','0')
                kwargs['sess_options']=options
            return original.InferenceSession(*args,**kwargs)

    module.onnxruntime=Runtime()
    return True
