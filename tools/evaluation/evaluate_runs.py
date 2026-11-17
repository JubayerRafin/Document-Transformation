"""Offline run inventory. Structural checks never imply semantic acceptance."""
import argparse
import json
from pathlib import Path
import pymupdf


def evaluate(root):
    latest={}
    for path in sorted(Path(root).glob('*/manifest.json')):
        m=json.loads(path.read_text(encoding='utf-8'))
        if m.get('status')=='generated':
            latest[(m['source_sha256'],m['target_language'])]=(path,m)
    rows=[]
    for path,m in latest.values():
        row=dict(source=m['source_name'],source_sha256=m['source_sha256'],target=m['target_language'],
                 run_id=m['run_id'],policy=m.get('integration_version','unknown'),
                 terminology=m.get('terminology',{}),semantic_quality='not_evaluated',
                 current_policy=m.get('integration_version','').startswith('generic-'))
        try:
            with pymupdf.open(m['output_pdf']) as pdf:
                row['opens']=True
                row['page_count_matches']=len(pdf)==m['pages']
        except Exception as e:row.update(opens=False,error=str(e))
        review=path.parent/'scan-reconstruction.json'
        if review.exists():
            records=json.loads(review.read_text(encoding='utf-8'))['regions']
            row['blocked_regions']=[r['id'] for r in records if r.get('reason') and not r['reason'].startswith('Translation unchanged;')]
        row['acceptance']='requires_review'
        rows.append(row)
    return {'evaluation_type':'offline_inventory_not_new_inference','runs':rows,
            'required_human_checks':['source-to-extraction transcription','translation meaning and omissions','identifier fidelity','layout and artwork','untranslated regions'],
            'held_out_documents':'not yet evaluated'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runs',default='runs/transformation');p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(evaluate(a.runs),ensure_ascii=False,indent=2),encoding='utf-8')
    print(out)
