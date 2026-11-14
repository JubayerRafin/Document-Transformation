"""Compare two scored runs of a PII evaluation set as a Markdown report.

usage: python -m evaluation.pii.compare <set_dir> <before_label> <after_label> [out.md]
Run evaluation.pii.score for both labels first.
"""
import json, sys
from pathlib import Path


def pct(v): return '—' if v is None else f'{100 * v:.1f}%'


def main():
    set_dir, a, b = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else set_dir / f'compare-{a}-vs-{b}.md'
    key = json.loads((set_dir / 'answer_key.json').read_text(encoding='utf-8'))
    S = {l: json.loads((set_dir / 'runs' / l / 'scores.json').read_text(encoding='utf-8')) for l in (a, b)}
    sa, sb = S[a]['summary'], S[b]['summary']
    lines = [f'# PII evaluation: `{a}` vs `{b}`', '',
             f"Answer key: {key['_meta'].get('review_status', 'unreviewed')}. "
             f"{sa['documents']} documents, {sa['expected_values']} values that must be masked, {sa['negatives']} that must stay visible.", '',
             '| Measure | ' + a + ' | ' + b + ' |', '|---|---|---|']
    rows = [('Documents produced', 'generated', str), ('Must-mask values found (any form)', 'recall_any', pct),
            ('Must-mask values found exactly', 'recall_full', pct), ('Must-mask values missed', 'missed_values', str),
            ('Text values still readable in output', 'text_leaks', str), ('…of text values checked', 'text_values_checked', str),
            ('Image values still readable (fresh OCR)', 'image_leaks', str), ('…of image values checked', 'image_values_checked_by_ocr', str),
            ('Must-stay-visible values flagged as PII', 'negatives_flagged_as_pii', str),
            ('Must-stay-visible values removed from text', 'negatives_removed_from_text', str),
            ('Other findings not in the key', 'unexpected_findings', str), ('Server seconds per page', 'server_seconds_per_page', str)]
    for label, k, f in rows: lines.append(f'| {label} | {f(sa.get(k))} | {f(sb.get(k))} |')
    for group in ('category', 'variant', 'language', 'kind'):
        lines += ['', f'### Recall by {group}', '', f'| {group} | values | {a} | {b} |', '|---|---|---|---|']
        for k in sb['recall_by'][group]:
            lines.append(f"| {k} | {sb['counts_by'][group][k]} | {pct(sa['recall_by'][group].get(k))} | {pct(sb['recall_by'][group][k])} |")
    lines += ['', '### Documents whose outcome changed', '', '| Document | ' + a + ' | ' + b + ' |', '|---|---|---|']
    def outcome(r):
        if r['status'] != 'generated': return f"**{r['status']}** {r.get('failure_code') or ''}".strip()
        missed = [x['value'] for x in r['expected'] if x['detected'] == 'missed']
        leaks = [x['value'] for x in r['expected'] if x['leak']]
        parts = [f'missed {len(missed)}' if missed else '', f'leaks {len(leaks)}' if leaks else '',
                 f"over-mask {len(r['negatives_found']) + len(r['negatives_removed'])}" if r['negatives_found'] or r['negatives_removed'] else '']
        return ', '.join(p for p in parts if p) or 'all masked, nothing over-masked'
    for name in S[b]['documents']:
        ra, rb = S[a]['documents'].get(name), S[b]['documents'][name]
        oa, ob = (outcome(ra) if ra else '—'), outcome(rb)
        if oa != ob: lines.append(f'| {name} | {oa} | {ob} |')
    lines += ['', '### Remaining misses and leaks in `' + b + '`', '']
    for name, r in S[b]['documents'].items():
        missed = [f"{x['category']}: `{x['value']}`" for x in r['expected'] if x['detected'] == 'missed']
        leaks = [f"`{x['value']}`" for x in r['expected'] if x['leak']]
        over = sorted(set(r['negatives_found']) | set(r['negatives_removed']))
        if r['status'] != 'generated': lines.append(f"- **{name}**: {r['status']} {r.get('failure_code') or ''}")
        elif missed or leaks or over:
            lines.append(f"- **{name}**: " + '; '.join(p for p in [('missed ' + ', '.join(missed)) if missed else '',
                                                                   ('readable ' + ', '.join(leaks)) if leaks else '',
                                                                   ('over-masked ' + ', '.join(f'`{o}`' for o in over)) if over else ''] if p))
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8'); print('wrote', out)


if __name__ == '__main__':
    main()
