"""Bounded fallback: translate prose while keeping exact source atoms in code.

Used only after normal translation and preservation retry both fail. It does
not infer a missing placeholder's location from a rejected translation, trim
hallucinations, or publish an untranslated paragraph as a successful recovery.
"""
import hashlib
import re
import time

from transformation.llm.protected_text import protect_values
from transformation.llm.translation_guard import validate_candidate


def recovery_parts(text, values=()):
    masked,tokens=protect_values(text,values)
    if not tokens:
        raise ValueError('Literal recovery requires protected source atoms.')
    parts=re.split('('+'|'.join(re.escape(t) for t in tokens)+')',masked)
    prose=[p for p in parts if p not in tokens and any(c.isalpha() for c in p)]
    if len(prose)>24 or any(len(p)>1500 for p in prose):
        raise ValueError('Literal recovery exceeds its bounded segment budget.')
    return parts,tokens


def _audit_rejection(source,candidate,reason,model,purpose):
    import json,os
    from pathlib import Path
    from uuid import uuid4
    folder=os.environ.get('TRANSFORMX_RUN_DIR')
    if folder:
        audit=Path(folder)/'rejected-translations';audit.mkdir(exist_ok=True)
        (audit/(uuid4().hex+'.json')).write_text(json.dumps({'source':source,'candidate':candidate,
            'reason':reason,'attempt':3,'model':model,'purpose':purpose},
            ensure_ascii=False,indent=2),encoding='utf8')


def _translate_piece(endpoint,model,core,language,values,source,digest,index,budget):
    from transformation.llm.ollama_native import chat,record_progress
    from transformation.llm.translategemma import messages
    scripts={'Korean':r'[\uac00-\ud7a3]', 'ko':r'[\uac00-\ud7a3]',
             'Japanese':r'[\u3040-\u30ff\u3400-\u9fff]', 'ja':r'[\u3040-\u30ff\u3400-\u9fff]',
             'Chinese (Simplified)':r'[\u3400-\u9fff]', 'zh':r'[\u3400-\u9fff]',
             'Bengali':r'[\u0980-\u09ff]', 'bn':r'[\u0980-\u09ff]',
             'Hindi':r'[\u0900-\u097f]', 'hi':r'[\u0900-\u097f]',
             'Arabic':r'[\u0600-\u06ff]', 'ar':r'[\u0600-\u06ff]'}
    totals={'prompt_eval_count':0,'eval_count':0}
    limit=min(budget,2 if len(core)<=80 and len(core.split())<=8 else 1)
    if limit<=0:raise ValueError('Literal recovery exceeds its bounded request budget.')
    for number in range(1,limit+1):
        prompt=messages(core,language,source=source)
        if number==2:
            prompt[0]['content']=('The final source is a natural-language connecting text fragment '
                'between protected values, not a name or code. Translate only that fragment into '
                +language+'. Do not copy the source word or invent adjacent values. '
                'Return just its translation.\n'+prompt[0]['content'])
        start=time.monotonic()
        record_progress('request_started',source_sha256=digest,attempt=3,
                        purpose='literal_segment_recovery',segment=index,segment_attempt=number,model=model)
        candidate,stats=chat(endpoint,model,prompt)
        for key in totals:totals[key]+=stats.get(key,0)
        record_progress('request_completed',source_sha256=digest,attempt=3,
                        purpose='literal_segment_recovery',segment=index,segment_attempt=number,model=model,
                        seconds=round(time.monotonic()-start,2),
                        prompt_tokens=stats.get('prompt_eval_count',0),output_tokens=stats.get('eval_count',0))
        # Wrong or invented protected values never receive this connector retry.
        try:validate_candidate(core,candidate,values)
        except ValueError as error:
            _audit_rejection(core,candidate,str(error),model,'literal_segment_recovery')
            record_progress('validation_failed',source_sha256=digest,attempt=3,
                            purpose='literal_segment_recovery',segment=index,segment_attempt=number,reason=str(error))
            raise
        reason=('Literal recovery left a prose segment untranslated.' if candidate.strip()==core else
                'Literal recovery did not produce the target script; review required.'
                if language in scripts and not re.search(scripts[language],candidate) else '')
        if not reason:return candidate,totals,number
        _audit_rejection(core,candidate,reason,model,'literal_segment_recovery')
        record_progress('validation_failed',source_sha256=digest,attempt=3,
                        purpose='literal_segment_recovery',segment=index,segment_attempt=number,reason=reason)
    raise ValueError(reason)


def recover_literals(endpoint, model, text, language, values=(), source='en'):
    from transformation.llm.ollama_native import record_progress
    parts,tokens=recovery_parts(text,values)
    totals={'prompt_eval_count':0,'eval_count':0}
    output=[]
    requests_used=0
    digest=hashlib.sha256(text.encode()).hexdigest()
    for index,part in enumerate(parts):
        if part in tokens:
            output.append(tokens[part])
            continue
        if not any(c.isalpha() for c in part):
            output.append(part)
            continue
        letters=[i for i,c in enumerate(part) if c.isalpha()]
        left,right=letters[0],letters[-1]+1
        core=part[left:right]
        leading,trailing=part[:left],part[right:]
        previous=tokens.get(parts[index-1],'') if index else ''
        following=tokens.get(parts[index+1],'') if index+1<len(parts) else ''
        if (re.fullmatch(r'[A-Z]{2,8}',core)
                and re.fullmatch(r'\d+(?:\.\d+)?',previous)
                and re.fullmatch(r'\{v\d+\}',following)):
            # A numeric measurement's uppercase label before a math subscript
            # is notation, not ordinary prose that requires a target script.
            output.append(part)
            continue
        author=r'(?:[A-Z]\.\s*)+[A-Z][A-Za-z\u2019\x27-]+'
        if re.fullmatch(author+r'(?:,\s*(?:and\s+)?'+author+r')+',core):
            # Bibliographic initials and surnames are names, not untranslated
            # prose. This source-only syntax never matches an ordinary label.
            output.append(part)
            continue
        from transformation.llm.localized_numbers import month_sequence
        months=month_sequence(core,values)
        if (language in {'Korean','ko'} and len(months)==1
                and re.fullmatch(r'[A-Za-z]+',core)
                and re.fullmatch(r'[12]\d{3}',following)):
            # A month directly followed by a four-digit year is a date label.
            # Reuse the guard's calendar lexicon instead of asking the model
            # to guess an isolated abbreviation without its protected year.
            localized=f'{months[0]}월'
            validate_candidate(core,localized,values)
            output.append(leading+localized+trailing)
            continue
        candidate,stats,used=_translate_piece(endpoint,model,core,language,values,source,
                                               digest,index,24-requests_used)
        requests_used+=used
        for key in totals:totals[key]+=stats.get(key,0)
        candidate=candidate.strip()
        # Preserve source edge punctuation outside the model, too. Otherwise
        # a dropped comma after a version suffix can merge it into the next ID.
        # Remove only an exact duplicate of that same punctuation, never prose.
        prefix,suffix=leading.strip(),trailing.strip()
        if prefix and candidate.startswith(prefix):candidate=candidate[len(prefix):].lstrip()
        if suffix and candidate.endswith(suffix):candidate=candidate[:-len(suffix)].rstrip()
        output.append(leading+candidate+trailing)
    result=''.join(output)
    try:
        validate_candidate(text,result,values)
    except ValueError as error:
        _audit_rejection(text,result,str(error),model,'literal_segment_recovery_final')
        raise
    record_progress('recovery_completed',source_sha256=digest,
                    method='source_literal_segments',review_required=True)
    return result,totals
