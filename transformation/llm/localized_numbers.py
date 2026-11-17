"""Narrow, source-backed allowances for words localized as numeric forms.

This does not normalize existing numeric literals, money, identifiers, or
explicit protected values. Unsupported forms still fail the literal guard.
"""
from collections import Counter
import re

from transformation.llm.protected_text import protect_values

_MONTHS=(('january','jan'),('february','feb'),('march','mar'),('april','apr'),
         ('may',),('june','jun'),('july','jul'),('august','aug'),
         ('september','sept','sep'),('october','oct'),('november','nov'),('december','dec'))
_MONTH_VALUES={name:index for index,names in enumerate(_MONTHS,1) for name in names}
_MONTH_WORD='|'.join(sorted(_MONTH_VALUES,key=len,reverse=True))
_MONTH_RUN=re.compile(r'(?<![A-Za-z0-9_])(?:(?:'+_MONTH_WORD+r')\.?)+(?![A-Za-z0-9_])',re.I)
_MONTH_PART=re.compile(_MONTH_WORD,re.I)
_SMALL='zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
_TENS='twenty thirty forty fifty sixty seventy eighty ninety'.split()
_VALUES={word:index for index,word in enumerate(_SMALL)}
_VALUES.update({word:(index+2)*10 for index,word in enumerate(_TENS)})
_UNITS='(?:'+'|'.join(_SMALL[1:10])+')'
_UNDER100='(?:(?:'+'|'.join(_TENS)+r')(?:[ -]'+_UNITS+')?|(?:'+'|'.join(_SMALL)+'))'
_UNDER1000='(?:'+_UNITS+r' hundred(?: (?:and )?'+_UNDER100+')?|'+_UNDER100+')'
_NUMBER=re.compile(r'(?<![\w-])(?:'+_UNDER1000+r' thousand(?: (?:and )?'+_UNDER1000+')?|'+_UNDER1000+r')(?!\w)(?!-(?:'+ '|'.join(_VALUES)+r')\b)',re.I)
_CALENDAR_NUMBER=re.compile(r'(?:(?<![\w.,+-])|(?<=[월月][-–—~]))(\d+)(?:\s*[-–—~]\s*(\d+))?\s*(?:월|月)',re.ASCII)


def _ordinary_text(text,values):
    masked,tokens=protect_values(text,values)
    for token in tokens:masked=masked.replace(token,' ')
    return masked


def word_quantities(text,values=()):
    result=Counter()
    ordinary=_ordinary_text(text,values)
    # In technical prose, non-zero is the same quantity expressed as non-0.
    # Keep this separate from subtraction and hyphenated numeric compounds.
    result[0]+=len(re.findall(r'\bnon-zero\b',ordinary,re.I))
    for match in _NUMBER.finditer(ordinary):
        total=current=0
        for word in re.split('[ -]',match.group().lower()):
            if word=='and':continue
            if word=='hundred':current*=100
            elif word=='thousand':total+=current*1000;current=0
            else:current+=_VALUES[word]
        result[total+current]+=1
    return result


def ordinal_quantities(text,values=()):
    result=Counter()
    for match in re.finditer(r'(?<![\w-])(\d+)(st|nd|rd|th)\b',_ordinary_text(text,values),re.I):
        number=int(match[1])
        suffix='th' if 10<=number%100<=20 else {1:'st',2:'nd',3:'rd'}.get(number%10,'th')
        if match[2].lower()==suffix:result[number]+=1
    return result


def month_sequence(text,values=()):
    text=_ordinary_text(text,values);matches=list(_MONTH_RUN.finditer(text));result=[]
    for match in matches:
        parts=list(_MONTH_PART.finditer(match.group()))
        for part in parts:
            name=part.group().lower()
            if name=='may' and len(parts)==1:
                # Do not reinterpret modal "may" as a calendar month.
                before=text[:match.start()];after=text[match.end():]
                calendar_context=(text.strip(' .').lower()=='may'
                    or re.search(r'(?:\b(?:in|during|month|until|through)|[·/\-–])\s*$',before,re.I)
                    or re.search(r'(?:'+_MONTH_WORD+r')\.?\s*—\s*$',before,re.I)
                    or re.match(r'\s*—\s*(?:'+_MONTH_WORD+r')\b',after,re.I)
                    or re.match(r'\s*(?:\d|[\-/–])',after))
                if not calendar_context:continue
            result.append(_MONTH_VALUES[name])
    return result


def permits_numeric_additions(source,target,additions,values=()):
    """Every extra bare integer needs one matching ordinary source expression."""
    if not additions:return True
    if any(not re.fullmatch(r'[0-9]+',literal) for literal in additions):return False
    source_months=month_sequence(source,values)
    months=Counter(source_months);quantities=word_quantities(source,values)
    # Restrict ordinal localization to explicit Korean ordinal suffixes.
    source_ordinals=ordinal_quantities(source,values)
    target_ordinals=Counter(int(m[1]) for m in re.finditer(r'(?<![\w.])(\d+)\s*(?:번째|차)(?![A-Za-z0-9])',target,re.ASCII))
    quantities.update(source_ordinals & target_ordinals)
    target_months=[int(group) for m in _CALENDAR_NUMBER.finditer(target) for group in m.groups() if group is not None]
    calendar=Counter(target_months)
    # Only permit quantity additions outside calendar-month positions.
    target_literals=Counter()
    for literal in protect_values(target,values)[1].values():
        if re.fullmatch(r'[0-9]+',literal):target_literals[int(literal)]+=1
    normalized=Counter()
    for literal,count in additions.items():normalized[int(literal)]+=count
    localized_calendar=Counter()
    for value,count in normalized.items():
        calendar_count=min(count,calendar[value],months[value])
        localized_calendar[value]=calendar_count
        ordinary_count=count-calendar_count
        non_calendar=max(0,target_literals[value]-calendar[value])
        if ordinary_count>quantities[value] or ordinary_count>non_calendar:return False
    # A numeric month range must retain the order of its source month words.
    ordered_source=iter(source_months)
    for value in target_months:
        if localized_calendar[value]:
            if not any(source_value==value for source_value in ordered_source):return False
            localized_calendar[value]-=1
    return True


def permits_numeric_omissions(source,target,omissions,values=()):
    """Allow verified lexical side counts in Korean print-mode terminology.

    This is a language equivalence, not a document exception. Explicit protected
    literals remain exact. Other quantities, identifiers and languages stay strict.
    """
    if not omissions:return True
    if any(value not in {'1','2'} for value in omissions):return False
    explicit=set(values)
    if any(value in explicit for value in omissions):return False
    # Explicit whole phrases are not eligible for localization either.
    source_masked=source
    for value in sorted(explicit,key=len,reverse=True):
        source_masked=source_masked.replace(value,' ')
    source_counts=Counter()
    for m in re.finditer(r'(?<![A-Za-z0-9_-])([12])[- ]sided\s+printing\b',source_masked,re.I):
        source_counts[m.group(1)]+=1
    target_counts=Counter('1' if m.group(1)=='단면' else '2'
        for m in re.finditer(r'(?<![가-힣A-Za-z0-9])(단면|양면)\s*인쇄',target))
    # Require equal semantic counts, including when both modes occur together.
    if target_counts!=source_counts:return False
    return all(count<=source_counts[value] for value,count in omissions.items())


def quantity_only_translation(source,target,values=()):
    """A number-word-only label may legitimately become digits only."""
    text=_ordinary_text(source,values)
    if any(c.isalpha() for c in _NUMBER.sub('',text)):return False
    expected=word_quantities(source,values)
    if not expected:return False
    actual=Counter()
    for literal in protect_values(target,values)[1].values():
        if not re.fullmatch(r'[0-9]+',literal):return False
        actual[int(literal)]+=1
    return actual==expected
