"""Bounded, exact-text context bridge; never guesses a geometry mapping."""
from collections import defaultdict
import json
import re


def key(text):
    return re.sub(r'\s+',' ',text).strip()


def masked_context_ir(ir,values):
    """Remove selected sensitive values from both prose and nested context."""
    from copy import deepcopy
    from transformation.validation.privacy import mask
    def clean(value):
        if isinstance(value,str): return mask(value,values)
        if isinstance(value,list): return [clean(v) for v in value]
        if isinstance(value,dict): return {k:clean(v) for k,v in value.items()}
        return value
    result=deepcopy(ir)
    for unit in result['units']:
        for region in unit['regions']+unit.get('locked_regions',[]):
            for field in ('text','cells','metadata'):
                if field in region: region[field]=clean(region[field])
    return result


def build(ir):
    candidates=defaultdict(set)
    local_candidates=defaultdict(set)
    for unit in ir['units']:
        for region in unit['regions']:
            matrix=region.get('cells',[])
            if not matrix: continue
            headers=[str(v or '') for v in matrix[0]]
            for row in matrix:
                for column,value in enumerate(row):
                    if not value: continue
                    evidence={'column_header':headers[column] if column<len(headers) else '',
                              'row_values':[str(v or '') for v in row]}
                    candidates[key(str(value))].add(json.dumps(evidence,ensure_ascii=False,sort_keys=True))
        regions=unit.get('locked_regions') or [r for r in unit['regions'] if r.get('text') and r.get('bbox')]
        headings=[r['text'] for r in sorted(regions,key=lambda r:(r['bbox'][1],r['bbox'][0]))[:3]]
        for region in regions:
            text=region.get('text',''); box=region.get('bbox')
            if not text or not box: continue
            nearby=[]
            for other in regions:
                ob=other.get('bbox')
                if other is region or not ob: continue
                horizontal=max(0,min(box[2],ob[2])-max(box[0],ob[0]))
                distance=abs((box[1]+box[3]-ob[1]-ob[3])/2)
                if horizontal>min(box[2]-box[0],ob[2]-ob[0])*.25 and distance<max(90,(box[3]-box[1])*6):
                    nearby.append((distance,other['text']))
            evidence={'document_headings':headings,'nearby_same_column':[t for _,t in sorted(nearby)[:4]],
                      'field_context':region.get('metadata',{}).get('row_context',{}),
                      'role':'table cell or header' if region.get('metadata',{}).get('table_parent') else 'document text or field'}
            # Prefer geometric local evidence over a generic duplicate table-cell
            # lookup, while still withholding genuinely ambiguous equal strings.
            serialized=json.dumps(evidence,ensure_ascii=False,sort_keys=True)
            local_candidates[key(text)].add(serialized)
    candidates.update(local_candidates)
    return {text:'\nSource extraction context (background only): '+next(iter(contexts))[:1400]
            for text,contexts in candidates.items() if len(contexts)==1}
