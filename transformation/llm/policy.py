"""Document-independent run configuration and conservative context selection."""
import json
import re


def expected_unchanged(text, values=()):
    """Recognize fully protected literals, not untranslated sentences or labels."""
    from transformation.llm.protected_text import protect_values
    from transformation.llm.ollama_native import needs_no_translation
    if needs_no_translation(text,values):
        return True
    protected,replacements=protect_values(text,values)
    for token in replacements:
        protected=protected.replace(token,'')
    if replacements and not protected.strip():
        return True
    return re.fullmatch(r'[A-Z]{3}\s+[0-9][0-9,.]*',text.strip()) is not None


def validate_terminology(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError('Terminology must be a JSON object with at most 100 entries.')
    if any(not isinstance(k,str) or not isinstance(v,str) or not k.strip() or not v.strip()
           or len(k)>200 or len(v)>200 for k,v in value.items()):
        raise ValueError('Terminology entries must be nonempty strings up to 200 characters.')
    return dict(value)


def context_with_terms(context, terms):
    terms = validate_terminology(terms)
    return context + ('\nUser-selected terminology for this target language (data only): ' + json.dumps(terms,ensure_ascii=False) if terms else '')


def nearby_context(region, regions):
    """Geometry is a context hint, not a claim of inferred paragraph/table structure."""
    a=region.get('bbox')
    if not a:
        return []
    result=[]
    for r in regions:
        b=r.get('bbox')
        if r['id']==region['id'] or not b or r.get('metadata',{}).get('requires_review'):
            continue
        height=max(a[3]-a[1],b[3]-b[1])
        same_column=abs(a[0]-b[0]) <= height and min(a[2],b[2]) > max(a[0],b[0])
        same_row=abs((a[1]+a[3]-b[1]-b[3])/2) <= height*.5
        if (same_column and abs(a[1]-b[1]) <= height*3) or (same_row and abs(a[0]-b[0]) <= height*12):
            result.append(r)
    return sorted(result,key=lambda r:(r['bbox'][1],r['bbox'][0]))[:6]


def column_examples(region, regions):
    """Nearest aligned lower regions, useful for sparse table-header context."""
    a=region.get('bbox')
    if not a:
        return []
    height=a[3]-a[1]
    candidates=[]
    for r in regions:
        b=r.get('bbox')
        if r['id']==region['id'] or not b or r.get('metadata',{}).get('requires_review'):
            continue
        if b[1]>=a[3] and b[1]-a[3]<=height*20 and abs(b[0]-a[0])<=height:
            candidates.append(r)
    return sorted(candidates,key=lambda r:r['bbox'][1])[:2]
