"""Translate the same segments with one translation model, for a comparison of models.

    python -m evaluation.suite.model_ab SEGMENTS.jsonl OUT.jsonl --endpoint URL/v1 --model NAME [--workers 32]

SEGMENTS is evaluation/suite/translation.py's file (src, source, target, ref when known). Each kept
segment (one with a reference, or four words or more) is sent with the engine's own TranslateGemma
prompt (transformation/llm/translategemma.messages) at temperature 0, without the engine's checks or
retries, so models are compared on their first answer alone. OUT holds the rows with mt set to this
model's answer; evaluation/suite/comet_score.py and metricx_score.py score it like any segment file.
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from transformation.pdf.runner import LANGUAGES


def keep(row):
    words = len(row['src'].split()) if row['source'] not in ('zh', 'ja') else len(row['src']) // 2
    return bool(row.get('ref')) or words >= 4


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('segments'); ap.add_argument('out')
    ap.add_argument('--endpoint', required=True); ap.add_argument('--model', required=True)
    ap.add_argument('--workers', type=int, default=32)
    args = ap.parse_args()
    from transformation.llm.translategemma import messages
    from transformation.llm.http_session import post_json
    names = {code: name for name, code in LANGUAGES.items()}
    rows, seen = [], set()
    for line in Path(args.segments).read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        key = (row['src'], row['source'], row['target'])
        if keep(row) and key not in seen and len(row['src']) <= 1800:
            seen.add(key); rows.append(row)

    def one(row):
        prompt = messages(row['src'], names[row['target']], '', row['source'], args.model)
        answer = post_json(args.endpoint.rstrip('/') + '/chat/completions',
                           {'model': args.model, 'messages': prompt, 'temperature': 0, 'max_tokens': 2048})
        return dict(row, mt=answer['choices'][0]['message']['content'].strip(), model=args.model)
    with ThreadPoolExecutor(args.workers) as pool:
        out = list(pool.map(one, rows))
    Path(args.out).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in out), encoding='utf-8')
    print(len(out), 'segments translated by', args.model)


if __name__ == '__main__':
    main()
