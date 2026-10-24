"""Check default ONNX threading with idle spinning disabled, on identical pixels."""
import json
import sys
import time
from pathlib import Path


def main(source,output):
    import numpy as np
    import onnxruntime as ort
    import pymupdf
    from babeldoc.assets.assets import get_doclayout_onnx_model_path
    from babeldoc.docvision.doclayout import OnnxModel
    with pymupdf.open(source) as pdf:
        page=pdf[0];scale=1024/max(page.rect.width,page.rect.height)
        pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),colorspace=pymupdf.csRGB,alpha=False)
        image=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)[:,:,::-1]
    constructor=ort.InferenceSession;expected=None;rows=[]
    for no_spin in (False,True,True,False):
        options=ort.SessionOptions()
        if no_spin:
            options.add_session_config_entry('session.intra_op.allow_spinning','0')
            options.add_session_config_entry('session.inter_op.allow_spinning','0')
        def session(*args,**kwargs):return constructor(*args,sess_options=options,**kwargs)
        ort.InferenceSession=session
        try:model=OnnxModel(str(get_doclayout_onnx_model_path()))
        finally:ort.InferenceSession=constructor
        elapsed=[];same=True;delta=0
        for _ in range(2):
            start=time.monotonic();boxes=model.predict(image)[0].boxes;elapsed.append(time.monotonic()-start)
            result=np.array([list(b.xyxy)+[b.conf,b.cls] for b in boxes])
            if expected is None:expected=result.copy()
            same=same and np.array_equal(result,expected)
            if result.shape!=expected.shape:delta=None
            elif delta is not None:delta=max(delta,float(np.abs(result-expected).max(initial=0)))
        rows.append({'disable_spinning':no_spin,'seconds':elapsed,'detections_identical':bool(same),'max_absolute_difference':delta})
        del model
    Path(output).write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))


if __name__=='__main__':main(*sys.argv[1:])
