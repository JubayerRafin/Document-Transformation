"""Lossless request-sized source segmentation outside protected literals."""
import re
from transformation.llm.protected_text import protect_values


def bounded_segments(text,values=(),limit=1500,max_segments=32):
    masked,tokens=protect_values(text,values)
    if len(text)<=limit and len(masked)<=limit:return [text]
    # Recover source offsets from the protection transform; a supplied value
    # may contain whitespace, so raw whitespace splitting is not safe.
    parts=re.split('('+'|'.join(re.escape(t) for t in tokens)+')',masked) if tokens else [text]
    boundaries=[];offset=0
    for part in parts:
        if part in tokens:offset+=len(tokens[part]);continue
        boundaries.extend(offset+m.end() for m in re.finditer(r'\s+',part))
        offset+=len(part)
    assert offset==len(text)
    boundaries=sorted(set(boundaries+[len(text)]))
    entries=[m.start() for m in re.finditer(r'(?<!\S)\[\d+\]\s+',text)]
    segments=[];start=0
    while start<len(text):
        candidates=[]
        for end in boundaries:
            if end<=start:continue
            if end-start>limit:break
            chunk=text[start:end]
            safe,_=protect_values(chunk,values)
            if len(safe)<=limit:candidates.append(end)
        if not candidates:
            raise ValueError('Source has an indivisible token or protected span beyond the translation request limit.')
        end=candidates[-1]
        if end<len(text):
            # Keep numbered entries and author-initial chains together when
            # there is a substantial structural boundary within the budget.
            entry_ends=[n for n in candidates if n in entries and n-start>=(end-start)*.5]
            sentences=[n for n in candidates if n-start>=(end-start)*.5
                       and re.search(r'[.!?。！？][\]\)\"\u201d\u2019]*\s*$',text[start:n])
                       and not re.search(r'\b[A-Z](?:\.-[A-Z])?\.\s*$',text[start:n])]
            if entry_ends:end=entry_ends[-1]
            elif sentences:end=sentences[-1]
        segments.append(text[start:end]);start=end
        if len(segments)>max_segments:
            raise ValueError('Source exceeds the bounded translation segment count.')
    assert ''.join(segments)==text
    return segments
