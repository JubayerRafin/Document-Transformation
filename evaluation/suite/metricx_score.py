"""Add MetricX-24 scores to a comet_score.py result file.

    python metricx_score.py SCORES.json --code METRICX_REPO --model DIR --tokenizer DIR [--batch 16] [--max-length 1536]

MetricX-24 (google/metricx-24-hybrid-*, Apache-2.0) is one model that scores with or without a
reference. Scores run from 0 (best) to 25 (worst):

  metricx_qe   source and translation only, every segment (reference-free, like COMET-KiWi)
  metricx_ref  with the reference as well, where there is one

METRICX_REPO is a checkout of github.com/google-research/metricx (its metricx24.models module holds
the regression head); the tokenizer is the matching mT5 one (google/mt5-xl for the XL model). Input
is written and trimmed as metricx24/predict.py does: "source: ... candidate: ... [reference: ...]",
cut at --max-length tokens, end-of-sequence token removed. The segments and the per pair, per kind
and overall summary in SCORES.json gain the two fields; the file is rewritten in place.
"""
import argparse
import json
import sys
from pathlib import Path


def scorer(args):
    import torch
    import transformers
    sys.path.insert(0, args.code)
    from metricx24 import models
    # The sentencepiece tokenizer itself: the fast one is converted from it, which needs a newer protobuf.
    tokenizer = transformers.AutoTokenizer.from_pretrained(args.tokenizer, use_fast=False)
    model = models.MT5ForRegression.from_pretrained(args.model, torch_dtype=torch.bfloat16).cuda().eval()

    def score(texts):
        encoded = [tokenizer(t, max_length=args.max_length, truncation=True)['input_ids'][:-1] for t in texts]
        order = sorted(range(len(encoded)), key=lambda i: len(encoded[i]))
        out = [None] * len(encoded)
        for start in range(0, len(order), args.batch):
            batch = order[start:start + args.batch]
            width = max(len(encoded[i]) for i in batch)
            ids = torch.full((len(batch), width), tokenizer.pad_token_id, dtype=torch.long)
            mask = torch.zeros((len(batch), width), dtype=torch.long)
            for row, i in enumerate(batch):
                ids[row, :len(encoded[i])] = torch.tensor(encoded[i])
                mask[row, :len(encoded[i])] = 1
            with torch.no_grad():
                predictions = model(input_ids=ids.cuda(), attention_mask=mask.cuda(), use_cache=False).predictions
            for i, value in zip(batch, predictions.float().tolist()):
                out[i] = round(value, 4)
        return out
    return score


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('scores')
    ap.add_argument('--code', required=True); ap.add_argument('--model', required=True); ap.add_argument('--tokenizer', required=True)
    ap.add_argument('--batch', type=int, default=16); ap.add_argument('--max-length', type=int, default=1536)
    args = ap.parse_args()
    data = json.loads(Path(args.scores).read_text(encoding='utf-8'))
    rows = data['segments']
    score = scorer(args)
    for row, value in zip(rows, score([f"source: {r['src']} candidate: {r['mt']}" for r in rows])):
        row['metricx_qe'] = value
    referenced = [r for r in rows if r.get('ref')]
    if referenced:
        texts = [f"source: {r['src']} candidate: {r['mt']} reference: {r['ref']}" for r in referenced]
        for row, value in zip(referenced, score(texts)):
            row['metricx_ref'] = value
    groups = {}
    for row in rows:
        for axis, name in (('pair', f"{row['source']}-{row['target']}"), ('kind', row.get('kind') or '?'), ('all', 'all')):
            groups.setdefault((axis, name), []).append(row)
    for (axis, name), items in groups.items():
        target = data['summary'].setdefault(axis, {}).setdefault(name, {})
        for key in ('metricx_qe', 'metricx_ref'):
            values = [r[key] for r in items if r.get(key) is not None]
            target[key] = round(sum(values) / len(values), 4) if values else None
        changed = [r['metricx_qe'] for r in items if r['src'] != r['mt']]
        target['metricx_qe_translated'] = round(sum(changed) / len(changed), 4) if changed else None
    Path(args.scores).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(data['summary']['all'], indent=1))


if __name__ == '__main__':
    main()
