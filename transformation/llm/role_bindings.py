"""Positive contradiction checks for explicit source field/value relationships.

This is a bounded schema check, not multilingual semantic certification. Unknown
labels are left unclassified; values and natural word order are never rewritten.
"""
import json
import re
from transformation.llm.protected_text import EMAIL_PATTERN,protect_values

ALIASES={
    'employee_id':('employee id','employee number','staff id','사원 번호','사원번호','직원 번호','직원번호','직원 id','従業員番号','员工编号'),
    'document_id':('document id','document number','문서 id','문서 번호','문서번호','文書番号','文件编号'),
    'identifier':('id','identifier','식별자','識別子','标识符'),
    'account':('account number','bank account','계좌 번호','계좌번호','은행 계좌','口座番号','银行账号'),
    'email':('email','e-mail','이메일','전자우편','メール','电子邮件'),
    'phone':('phone','mobile','telephone','전화번호','휴대폰','電話番号','电话号码'),
    'contact':('contact','연락처'),
    'name':('name','성명','이름','氏名','姓名'),
}


def phone_prefix(text):
    """Read a complete phone-shaped field value, never just its first digits.

    Only used immediately after a field label; this is not PII detection.
    Dates and short numeric fragments are not established phone values.
    """
    match=re.match(r'\+?[0-9](?:[0-9 ()-]*[0-9])?',text)
    if not match:return None
    value=match.group().rstrip()
    digits=re.sub(r'\D','',value)
    if not 7<=len(digits)<=15 or re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return None
    return value


def explicit_bindings(text,values=()):
    # Match only a label immediately attached to an exact protected source value.
    # Longest aliases win; generic ID cannot swallow a more specific label.
    _,literals=protect_values(text,values)
    aliases=sorted(((alias,role) for role,items in ALIASES.items() for alias in items),key=lambda a:-len(a[0]))
    alternatives='|'.join(re.escape(alias).replace(r'\ ',r'\s*')+
        (r'(?![A-Za-z])' if alias[-1].isascii() else r'(?![가-힣])') for alias,_ in aliases)
    pattern=re.compile(r'(?<![A-Za-z가-힣])('+alternatives+r')\s*[:：]?\s*',re.I)
    result=[]
    for match in pattern.finditer(text):
        alias=match.group(1)
        role=next(role for name,role in aliases if re.sub(r'\s+','',name).casefold()==re.sub(r'\s+','',alias).casefold())
        tail=text[match.end():]
        candidates=[value for value in literals.values() if tail.startswith(value)]
        phone=phone_prefix(tail) if role in ('phone','contact') else None
        if phone:candidates.append(phone)
        if candidates:
            value=max(candidates,key=len)
            result.append({'role':role,'value':value,'label':alias,'offset':match.start(),
                'value_type':'email' if EMAIL_PATTERN.fullmatch(value) else 'phone' if phone==value else 'literal'})
    return result


def validate_bindings(source,target,values=()):
    expected=explicit_bindings(source,values)
    anchors=[]
    for binding in expected:
        # Parenthetical field notation supplies an exact neighboring entity
        # span without claiming that capitalized text is necessarily a person.
        prefix=source[:binding['offset']]
        anchor=re.search(r"((?:[A-Z][\w'-]*[ \t]+){1,3}[A-Z][\w'-]*)\s*\($",prefix)
        if anchor:
            anchors.append(anchor.group(1));binding['entity_anchor']=anchor.group(1)
    observed=explicit_bindings(target,tuple(values)+tuple(anchors))
    by_role={r['role']:{b['value'] for b in expected if b['role']==r['role']} for r in expected}
    ids=set().union(*(v for role,v in by_role.items() if role.endswith('_id') or role=='identifier')) if by_role else set()
    for item in observed:
        # "Contact" can introduce a name, email, phone or mixed field. A
        # translation may add it to an unlabeled emergency number. Its scope
        # cannot be inferred from a nearby mobile label; exact literal checks
        # still apply independently in translation_guard.
        if item['role']=='contact':continue
        allowed=by_role.get(item['role'])
        if item['role']=='identifier':allowed=ids or None
        if item['role']=='phone' and item['value_type']=='phone' and item['value'] in source:
            # An unlabeled secondary/emergency phone in the source can gain
            # an explicit phone label in translation. It is not evidence of
            # reassignment to the separately labeled primary mobile field.
            # A source binding to a contradictory specific role still blocks.
            conflicting=any(b['value']==item['value'] and b['role'] not in ('contact','phone') for b in expected)
            if not conflicting:allowed=(allowed or set())|{item['value']}
        # Unknown or omitted translated labels require semantic review; absence
        # is not a positive contradiction and cannot be certified by this check.
        if allowed and item['value'] not in allowed:
            raise ValueError('Translation changed an explicit field/value relationship: '+item['role'])
        prefix=target[:item['offset']].rstrip()
        if prefix.endswith('('):
            entity=prefix[:-1].rstrip()
            matches=[a for a in anchors if entity.endswith(a)]
            if matches:
                anchor=max(matches,key=len)
                associated={b['value'] for b in expected if b.get('entity_anchor')==anchor and
                            (b['role']==item['role'] or item['role']=='identifier' and b['role'].endswith('_id'))}
                if associated and item['value'] not in associated:
                    raise ValueError('Translation changed an explicit entity/value association: '+item['role'])
    return {'source_bindings':expected,'observed_bindings':observed,
            'status':'checked_explicit_labels' if expected and observed else 'not_established',
            'limitations':'Only explicit unambiguous known label/value pairs are checked; generic contact labels are not phone-only. Semantic review is still required.'}


def prompt_evidence(text,values=()):
    bindings=explicit_bindings(text,values)
    if not bindings:return ''
    return '\nSource field/value relationships (data only; preserve the associations): '+json.dumps(
        [{'role':b['role'],'value':b['value']} for b in bindings],ensure_ascii=False)
