"""Compare fresh versus shared ONNX sessions on identical rendered PDF pages."""
import argparse
import json
from pathlib import Path
import time


def main(config_path, output):
    import numpy as np
    import pymupdf
    from babeldoc.docvision.doclayout import OnnxModel
    from babeldoc.assets.assets import get_doclayout_onnx_model_path
    from transformation.pdf.layout_service import shared_layout, install
    import sys
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    images = []
    # Include every page, retaining dimensions and vendor preprocessing.
    for document in config['documents']:
        with pymupdf.open(document['path']) as pdf:
            for number, page in enumerate(pdf):
                pix = page.get_pixmap(matrix=pymupdf.Matrix(1024/max(page.rect.width,page.rect.height),
                    1024/max(page.rect.width,page.rect.height)), colorspace=pymupdf.csRGB, alpha=False)
                image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,pix.width,3)[:,:,::-1]
                images.append((document['id'], number+1, image))
    original = OnnxModel.__init__
    path = str(get_doclayout_onnx_model_path())
    expected = []
    for name, number, image in images:
        start = time.monotonic()
        model = OnnxModel(path)
        initialized = time.monotonic()
        boxes = model.predict(image)[0].boxes
        # Preserve raw detections rather than rounded or IoU-only equivalence.
        expected.append(np.array([list(box.xyxy) + [box.conf, box.cls] for box in boxes]))
        rows.append({'document': name, 'page': number, 'fresh_init_seconds': initialized-start,
            'fresh_predict_seconds': time.monotonic()-initialized})
        del model
    with shared_layout(sys.executable, output/'service') as metadata:
        try:
            install()
            for index, (_, _, image) in enumerate(images):
                start = time.monotonic()
                model = OnnxModel(path)
                initialized = time.monotonic()
                boxes = model.predict(image)[0].boxes
                rows[index].update(shared_init_seconds=initialized-start,
                    shared_predict_seconds=time.monotonic()-initialized,
                    detections_identical=bool(np.array_equal(
                        np.array([list(box.xyxy) + [box.conf, box.cls] for box in boxes]), expected[index])))
        finally:
            OnnxModel.__init__ = original
            del OnnxModel._transformx_shared_layout
    (output/'report.json').write_text(json.dumps({'service':metadata,'pages':rows},indent=2),encoding='utf-8')
    print(json.dumps(rows,indent=2))
    if not all(row['detections_identical'] for row in rows):
        raise RuntimeError('Shared model changed layout detections.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    main(args.config,args.output)
