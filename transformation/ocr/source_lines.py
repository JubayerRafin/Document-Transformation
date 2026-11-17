"""Join tightly adjacent OCR word fragments with measured line geometry.

This does not join rows, table cells, fields, uncertain text or protected values.
Raw recognition evidence is retained; derived groups record their source IDs.
"""
import re
from transformation.llm.protected_text import protect_values


def group_words(ir,values=()):
    groups=[];updates={}
    for unit_index,unit in enumerate(ir['units']):
        if not unit.get('ocr_folder'):continue
        regions=unit['regions']
        if len(regions)>2000:continue
        eligible=[]
        for index,r in enumerate(regions):
            meta=r.get('metadata',{});text=r.get('text','');box=r.get('bbox')
            if (r.get('kind')!='text' or meta.get('requires_review') or meta.get('cell_bbox')
                or meta.get('parent_line_id') or meta.get('source_value_preserved')
                or not re.fullmatch(r"[A-Za-z][A-Za-z '&’,-]*",text) or protect_values(text,values)[1]
                or not box or box[2]<=box[0] or box[3]<=box[1]):continue
            eligible.append(index)
        rights={};lefts={}
        for i in eligible:
            a=regions[i]['bbox'];ha=a[3]-a[1]
            for j in eligible:
                if i==j:continue
                b=regions[j]['bbox'];hb=b[3]-b[1];gap=b[0]-a[2]
                if (0<=gap<=.28*min(ha,hb) and .8<=ha/hb<=1.25
                    and abs((a[1]+a[3]-b[1]-b[3])/2)<=.15*max(ha,hb)
                    and min(a[3],b[3])-max(a[1],b[1])>=.75*min(ha,hb)):
                    rights.setdefault(i,[]).append(j);lefts.setdefault(j,[]).append(i)
        chains=[];used=set()
        for i in eligible:
            if i in used or lefts.get(i):continue
            chain=[i]
            while len(rights.get(chain[-1],[]))==1:
                j=rights[chain[-1]][0]
                if len(lefts.get(j,[]))!=1 or j in chain or j in used:break
                chain.append(j)
            if len(chain)<2 or not any(len(regions[j]['text'].split())==1 for j in chain):continue
            text=' '.join(regions[j]['text'].strip() for j in chain)
            if len(text)>1500:continue
            used.update(chain);chains.append(chain)
        if not chains:continue
        replacements={};removed=set()
        for chain in chains:
            members=[regions[j] for j in chain];first=members[0]
            box=[min(r['bbox'][0] for r in members),min(r['bbox'][1] for r in members),
                 max(r['bbox'][2] for r in members),max(r['bbox'][3] for r in members)]
            ids=[r['id'] for r in members];text=' '.join(r['text'].strip() for r in members)
            metadata={**first.get('metadata',{}),'source_word_region_ids':ids,
                      'source_word_regions':members,'grouping_basis':'tight_same_baseline_unambiguous_word_fragments'}
            replacements[chain[0]]={**first,'bbox':box,'text':text,'metadata':metadata}
            removed.update(chain[1:])
            groups.append({'page':unit['number'],'region_id':first['id'],'source_region_ids':ids,'source_text':text,'bbox':box})
        updates[unit_index]={**unit,'regions':[replacements.get(j,r) for j,r in enumerate(regions) if j not in removed]}
    if not updates:return ir,[]
    return {**ir,'units':[updates.get(i,u) for i,u in enumerate(ir['units'])]},groups
