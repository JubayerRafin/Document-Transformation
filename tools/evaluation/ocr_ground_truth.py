"""Reviewed, detection-conditional OCR CER/WER. Never invent reference text."""
import argparse
import hashlib
import json
from pathlib import Path
import unicodedata


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]


def inventory(report):
    rows=[]
    for document in report['documents']:
        digest=document.get('source_sha256','')
        if len(digest)!=64:raise ValueError('OCR annotation requires a frozen source SHA256 for every document.')
        for asset in document['after']:
            for line in asset['transcripts']:
                rows.append({'key':document['document']+'/'+asset['asset']+'/'+line['id'],
                    'source_sha256':digest,'bbox':line['bbox'],'hypothesis':line['text'],'requires_review':line['requires_review']})
    if len({r['key'] for r in rows})!=len(rows):raise ValueError('Duplicate OCR annotation keys.')
    return rows


def evidence_digest(path):
    # References are bound to frozen source regions, not a model hypothesis or
    # confidence flag. A new recognition prediction can be scored fairly against
    # the same reviewed references when source and detection geometry are stable.
    geometry=[{k:row[k] for k in ('key','source_sha256','bbox')} for row in inventory(read(path))]
    return hashlib.sha256(json.dumps(geometry,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def prepare(report_path,destination):
    report_path=Path(report_path);destination=Path(destination)
    rows=inventory(read(report_path))
    data={'schema':'transformx-ocr-reference-1','evidence_sha256':evidence_digest(report_path),
        'scope':'conditional_on_detected_lines','reviewer':'','reviewed':False,
        'normalization':'Unicode NFC only; case and punctuation are significant; WER uses whitespace tokens.',
        'limitations':'Does not measure undetected text, detection recall, reading order or table structure.',
        'lines':[{**row,'reference':None,'reviewed':False} for row in rows]}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    return data


def evaluate(report_path,annotation_path):
    report_path=Path(report_path);data=read(annotation_path)
    if data.get('evidence_sha256')!=evidence_digest(report_path):raise ValueError('OCR evidence changed; annotation version does not match.')
    if data.get('schema')!='transformx-ocr-reference-1' or data.get('scope')!='conditional_on_detected_lines':raise ValueError('Unsupported OCR reference scope.')
    if data.get('reviewed') is not True or not str(data.get('reviewer','')).strip():raise ValueError('Independent reference review and reviewer are required.')
    actual={r['key']:r for r in inventory(read(report_path))}
    lines=data.get('lines',[])
    if len(lines)!=len(actual) or {r['key'] for r in lines}!=set(actual):raise ValueError('Missing, duplicate or invented OCR annotation keys.')
    edits=words=characters=word_edits=exact=0;results=[]
    for row in lines:
        if row.get('reviewed') is not True or not isinstance(row.get('reference'),str):raise ValueError('Every OCR reference must be explicitly reviewed.')
        reference=unicodedata.normalize('NFC',row['reference']);hypothesis=unicodedata.normalize('NFC',actual[row['key']]['hypothesis'])
        if len(reference)>10000 or len(hypothesis)>10000 or len(reference)*len(hypothesis)>4_000_000:
            raise ValueError('OCR line exceeds evaluation length/work limit.')
        ce=distance(reference,hypothesis);we=distance(reference.split(),hypothesis.split())
        edits+=ce;characters+=len(reference);word_edits+=we;words+=len(reference.split());exact+=reference==hypothesis
        results.append({'key':row['key'],'character_edits':ce,'word_edits':we,'exact':reference==hypothesis})
    return {'scope':data['scope'],'reviewer':data['reviewer'],'lines':len(lines),'exact_lines':exact,
        'character_edits':edits,'reference_characters':characters,'cer':edits/characters if characters else None,
        'word_edits':word_edits,'reference_words':words,'wer':word_edits/words if words else None,
        'normalization':data.get('normalization'),'limitations':data.get('limitations'),'details':results}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','evaluate'])
    p.add_argument('--report',required=True);p.add_argument('--annotations',required=True);p.add_argument('--output')
    a=p.parse_args()
    if a.action=='prepare':prepare(a.report,a.annotations);print(a.annotations)
    else:
        result=evaluate(a.report,a.annotations)
        if a.output:Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        else:print(json.dumps(result,ensure_ascii=True,indent=2))
