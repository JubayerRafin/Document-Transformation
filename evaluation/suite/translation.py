"""Translation segments of website jobs, with references where the source is known statements.

    python -m evaluation.suite.translation collect RESULTS user@host -p PORT -i KEY ...
    python -m evaluation.suite.translation segments RESULTS OUT.jsonl

collect copies every job's translate_tracking.json (BabelDOC's record of each paragraph sent to the
model and its answer) and, for Word and PowerPoint jobs, office-translation.json (each translated
piece) from the server's job folders into RESULTS/server/<job>/.

segments writes one JSON line per translated paragraph: doc, source, target, kind, src, mt and, when
the paragraph consists of the corpus's twelve statements (make_fullpage.SENTENCES, written in all
eleven languages), ref: the same statements in the target language with the source's numbers. Formula
placeholders ({v1}) are removed from both sides. evaluation/suite/comet_score.py scores the file.
"""
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path


def summaries(results):
    for path in sorted(Path(results).rglob('summary.json')):
        if 'server' in path.parts:
            continue
        s = json.loads(path.read_text(encoding='utf-8'))
        if s.get('job'):
            yield path, s


def collect(results, ssh):
    results = Path(results)
    ids = ' '.join(s['job'] for _, s in summaries(results))
    command = (f'cd /workspace/transformx-app/runs/local && find {ids} \\( -path "*work/source/translate_tracking.json" '
               f'-o -name office-translation.json -o -name office-fitting.json \\) -print0 2>/dev/null | tar --null -czf - -T -')
    archive = results / 'tracking.tgz'
    with open(archive, 'wb') as out:
        subprocess.run(['ssh', *ssh, command], stdout=out, check=True)
    with tarfile.open(archive) as tar:
        tar.extractall(results / 'server', filter='data')


def pairs(node):
    """(input, output) of every paragraph record in a translate_tracking.json tree."""
    if isinstance(node, dict):
        if isinstance(node.get('input'), str) and isinstance(node.get('output'), str) and 'pdf_unicode' in node:
            yield node['input'], node['output']
        for value in node.values():
            yield from pairs(value)
    elif isinstance(node, list):
        for value in node:
            yield from pairs(value)


def clean(text):
    return ' '.join(re.sub(r'\{v\d+\}', ' ', text).split())


def statement_patterns(language):
    from evaluation.robustness.make_fullpage import SENTENCES
    patterns = []
    for i, template in enumerate(SENTENCES.get(language, [])):
        pieces = []
        for piece in re.split(r'(\{pct\}|\{n\})', template):
            if piece in ('{pct}', '{n}'):
                pieces.append(r'(\d+(?:[.,]\d+)?)')
            elif piece.strip():
                pieces.append(r'\s*'.join(re.escape(ch) for ch in piece if not ch.isspace()))
        patterns.append((i, re.compile(r'\s*'.join(pieces))))
    return patterns


def reference(src, source, target):
    """The target-language statements making up src, or None when src is not made of statements."""
    from evaluation.robustness.make_fullpage import SENTENCES
    if source not in SENTENCES or target not in SENTENCES:
        return None
    found = []
    for i, pattern in statement_patterns(source):
        for m in pattern.finditer(src):
            found.append((m.start(), m.end(), i, m.groups()))
    found.sort()
    kept, end = [], -1
    for item in found:
        if item[0] >= end:
            kept.append(item); end = item[1]
    covered = sum(b - a for a, b, _, _ in kept)
    if not kept or covered < 0.9 * len(re.sub(r'\s', '', src)) - 2 and covered < 0.9 * len(src):
        return None
    out = []
    for _, _, i, numbers in kept:
        text, numbers = SENTENCES[target][i], list(numbers)
        for slot in re.findall(r'\{pct\}|\{n\}', text):
            text = text.replace(slot, numbers.pop(0) if numbers else '', 1)
        out.append(text)
    return (' ' if target not in ('ja', 'zh') else '').join(out), [i for _, _, i, _ in kept]


def segments(results, out):
    rows = []
    for path, s in summaries(results):
        folder = Path(results) / 'server' / s['job']
        found = [(src, mt) for file in folder.rglob('translate_tracking.json')
                 for src, mt in pairs(json.loads(file.read_text(encoding='utf-8')))]
        # Word and PowerPoint jobs record their translated pieces in office-translation.json.
        found += [(row['src'], row['mt']) for file in folder.rglob('office-translation.json')
                  for row in json.loads(file.read_text(encoding='utf-8')).get('pairs', [])]
        seen = set()
        for src, mt in found:
            src, mt = clean(src), clean(mt)
            if not src or not any(c.isalpha() for c in src) or (src, mt) in seen:
                continue
            seen.add((src, mt))
            row = {'doc': s.get('doc'), 'source': s.get('source'), 'target': s.get('target'), 'kind': s.get('kind'),
                   'src': src, 'mt': mt}
            ref = reference(src, s.get('source'), s.get('target'))
            if ref:
                row['ref'], row['statements'] = ref
            rows.append(row)
    Path(out).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    print(len(rows), 'segments,', sum('ref' in r for r in rows), 'with references')


if __name__ == '__main__':
    if sys.argv[1] == 'collect':
        collect(sys.argv[2], sys.argv[3:])
    else:
        segments(sys.argv[2], sys.argv[3])
