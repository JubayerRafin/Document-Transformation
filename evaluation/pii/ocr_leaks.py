"""Read the redacted output pages again with GPU OCR, so values that live in pixels can be checked.

For every document whose answer key has image values and whose run produced an output, each
output page is rendered here with the product's raster policy, all pages are uploaded in one
batch, read with PaddleOCR on the GPU server (same detector as the product), and the text is
saved as <set_dir>/runs/<label>/<doc>/output-ocr.json  ({"pages": {"1": "line\\nline"}}).
score.py then counts an image value as leaked when it is still readable there.

usage: python -m evaluation.pii.ocr_leaks <set_dir> <label> --host H --port P [--key ~/.ssh/id] [name ...]
"""
import argparse, json, subprocess, tarfile, tempfile, uuid
from pathlib import Path

import pymupdf

POD_SCRIPT = r'''
import json, sys
from pathlib import Path
from paddleocr import PaddleOCR
ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_server_det", text_recognition_model_name="en_PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, device="gpu:0")
folder = Path(sys.argv[1]); out = {}
for image in sorted(folder.glob("*.png")):
    lines = []
    for res in ocr.predict(str(image)):
        lines += [t for t in res["rec_texts"] if t.strip()]
    out[image.stem] = "\n".join(lines)
(folder / "ocr.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print("pages", len(out))
'''


def main():
    from tools.ocr.raster_policy import render_page
    from transformation.remote_pipeline import connection
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('set_dir'); ap.add_argument('label'); ap.add_argument('names', nargs='*')
    ap.add_argument('--host'); ap.add_argument('--port', type=int)
    ap.add_argument('--key', default='~/.ssh/id_ed25519')
    ap.add_argument('--local', action='store_true', help='run on the GPU server itself (no SSH)')
    args = ap.parse_args()
    set_dir = Path(args.set_dir).resolve()
    key = json.loads((set_dir / 'answer_key.json').read_text(encoding='utf-8'))
    if not args.local: ssh, scp, target = connection({'host': args.host, 'port': args.port, 'key': args.key})
    with tempfile.TemporaryDirectory(prefix='pii-ocr-') as temp:
        temp = Path(temp); pages = temp / 'pages'; pages.mkdir(); docs = {}
        for name, spec in key.items():
            if name.startswith('_') or (args.names and name not in args.names): continue
            if not any(where == 'image' for _, _, where in spec['expected']): continue
            try: summary = json.loads((set_dir / 'runs' / args.label / name / 'summary.json').read_text(encoding='utf-8'))
            except (OSError, ValueError): continue
            output = Path(summary.get('output_file') or '')
            if summary.get('status') != 'generated' or not output.is_file(): continue
            with pymupdf.open(output) as pdf:
                for index, page in enumerate(pdf):
                    render_page(page)[0].save(pages / f'{name}__{index + 1}.png')
                docs[name] = len(pdf)
        if not docs: print('nothing to check'); return
        (pages / 'run_ocr.py').write_text(POD_SCRIPT, encoding='utf-8')
        if args.local:
            subprocess.run(['/workspace/paddle-env/bin/python', str(pages / 'run_ocr.py'), str(pages)], check=True, timeout=3600)
            texts = json.loads((pages / 'ocr.json').read_text(encoding='utf-8'))
        else:
            texts = None
        if texts is None:
            with tarfile.open(temp / 'pages.tar', 'w') as tar: tar.add(pages, arcname='pages')
            remote = '/workspace/eval-ocr/' + uuid.uuid4().hex
            subprocess.run([*ssh, f'mkdir -p {remote}'], check=True)
            subprocess.run([*scp, str(temp / 'pages.tar'), f'{target}:{remote}/pages.tar'], check=True)
            subprocess.run([*ssh, f'cd {remote} && tar -xf pages.tar && /workspace/paddle-env/bin/python pages/run_ocr.py pages'],
                           check=True, timeout=3600)
            subprocess.run([*scp, f'{target}:{remote}/pages/ocr.json', str(temp / 'ocr.json')], check=True)
            subprocess.run([*ssh, f'rm -rf {remote}'], check=False)
            texts = json.loads((temp / 'ocr.json').read_text(encoding='utf-8'))
    for name, count in docs.items():
        result = {'pages': {str(n): texts.get(f'{name}__{n}', '') for n in range(1, count + 1)}}
        (set_dir / 'runs' / args.label / name / 'output-ocr.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(args.label, 'OCR-checked documents:', len(docs), 'pages:', sum(docs.values()), flush=True)


if __name__ == '__main__':
    main()
