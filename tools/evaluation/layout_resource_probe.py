"""Alternate ONNX CPU policies on one identical rendered page, without LLM calls."""
import argparse
import json
from pathlib import Path
import time


def main(pdf_path, output):
    import gc
    import numpy as np
    import onnxruntime as ort
    import pymupdf
    from babeldoc.assets.assets import get_doclayout_onnx_model_path
    from babeldoc.docvision.doclayout import OnnxModel
    with pymupdf.open(pdf_path) as pdf:
        page=pdf[0]
        scale=1024/max(page.rect.width,page.rect.height)
        pix=page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),colorspace=pymupdf.csRGB,alpha=False)
        image=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width,3)[:,:,::-1]
    path=str(get_doclayout_onnx_model_path())
    constructor=ort.InferenceSession
    expected=None
    rows=[]
    for threads in (0,1,2,4,4,2,1,0):
        options=ort.SessionOptions()
        if threads:
            options.intra_op_num_threads=threads
            options.inter_op_num_threads=1
            options.add_session_config_entry('session.intra_op.allow_spinning','0')
            options.add_session_config_entry('session.inter_op.allow_spinning','0')
        def session(*args,**kwargs):
            return constructor(*args,sess_options=options,**kwargs)
        ort.InferenceSession=session
        started=time.monotonic()
        try:model=OnnxModel(path)
        finally:ort.InferenceSession=constructor
        initialization=time.monotonic()-started
        durations=[]
        same=True
        for _ in range(2):
            started=time.monotonic()
            boxes=model.predict(image)[0].boxes
            durations.append(time.monotonic()-started)
            result=np.array([list(box.xyxy)+[box.conf,box.cls] for box in boxes])
            if expected is None:expected=result.copy()
            same=same and np.array_equal(result,expected)
        rows.append({'threads':threads,'initialization_seconds':initialization,
            'prediction_seconds':durations,'detections_identical':bool(same)})
        del model
        gc.collect()
    Path(output).write_text(json.dumps(rows,indent=2),encoding='utf-8')
    print(json.dumps(rows,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    main(args.pdf,args.output)
