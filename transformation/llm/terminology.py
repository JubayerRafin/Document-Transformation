"""Source-grounded terminology shared across text, tables and image workers."""
import re
from transformation.llm.policy import validate_terminology


def schema_terms(ir,configured=None):
    terms=validate_terminology(configured);proposals=[]
    headers={'product','product name','model','model name','item','제품','모델','품목'}
    for unit in ir['units']:
        for region in unit['regions']:
            matrix=region.get('cells') or []
            if not matrix:continue
            normalized=[str(h or '').strip().casefold() for h in matrix[0]]
            product_schema=any(h in {'sku','product code','model code','제품 코드'} for h in normalized)
            columns=[i for i,h in enumerate(normalized) if h in headers and (h!='item' or product_schema)]
            for row in matrix[1:]:
                for column in columns:
                    if column>=len(row):continue
                    value=str(row[column] or '').strip()
                    model_token=re.search(r'\b[A-Za-z]+[A-Za-z-]*\d[A-Za-z0-9-]*\b',value)
                    brand_and_number=re.search(r'\b[A-Z][a-z]+[A-Z][A-Za-z]*\s+\d+\b',value)
                    if (2<=len(value)<=100 and len(value.split())<=4 and '\n' not in value
                        and (model_token or brand_and_number) and not re.search(r'[@:$€£¥₩]',value)):
                        proposals.append({'source':value,'unit':unit['number'],'region_id':region['id'],
                                          'basis':'explicit_product_column_with_model_token'})
                        if len(terms)<100:terms.setdefault(value,value)
    return terms,proposals


def prepare(source,terms,values=()):
    terms=validate_terminology(terms)
    if not terms:return source,[],[]
    pattern=re.compile(r'(?<!\w)(?:'+'|'.join(re.escape(s) for s in sorted(terms,key=lambda s:-len(s)))+r')(?!\w)')
    applied=[]
    def replace(match):
        original=match.group(0);target=terms[original]
        # A glossary cannot authorize changing an identifier or protected value.
        from transformation.llm.translation_guard import validate_candidate
        validate_candidate(original,target,values)
        applied.append({'source':original,'target':target})
        return target
    prepared=pattern.sub(replace,source)
    return prepared,list(dict.fromkeys(t['target'] for t in applied)),applied
