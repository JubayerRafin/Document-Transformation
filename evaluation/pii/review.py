"""Build a self-contained HTML page for a person to review the answer key, and apply their corrections.

usage:
  python -m evaluation.pii.review build <set_dir>            -> <set_dir>/review.html (local, single reviewer)
  python -m evaluation.pii.review apply <set_dir> <review.json>  -> answer_key.json marked reviewed (backup kept)
"""
import base64, html, json, sys
from pathlib import Path

import pymupdf

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PII Answer Key Review</title><style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#5d6675;--line:#d9dde4;--accent:#2457c5;--bad:#b42318}
body{margin:0;font:14px/1.45 system-ui,Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
header h1{font-size:16px;margin:0 auto 0 0}button{font:inherit;padding:6px 12px;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:6px;cursor:pointer}
main{max-width:1200px;margin:0 auto;padding:16px}section{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:0 0 16px;padding:12px 16px}
section.done{border-color:#7fb48a}h2{font-size:15px;margin:0 0 4px}.meta{color:var(--muted);margin:0 0 10px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}@media(max-width:800px){.grid{grid-template-columns:1fr}}
.pages img{width:100%;border:1px solid var(--line);margin-bottom:8px}table{width:100%;border-collapse:collapse;font-size:13px}
td,th{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:600}
td.v{font-family:Consolas,monospace;word-break:break-all}label.flag{white-space:nowrap;color:var(--bad)}textarea{width:100%;min-height:52px;font:inherit;box-sizing:border-box}
.ok{display:flex;gap:6px;align-items:center;margin-top:8px;font-weight:600}
</style></head><body><header><h1>PII answer key review &middot; <span id="progress"></span></h1>
<span>Tick a box only where the key is wrong. Then mark the document reviewed.</span><button id="save">Download review.json</button></header><main id="main"></main>
<script>
const DATA=__DATA__;const KEY='pii-review-v1';let state={};try{state=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
const save=()=>{try{localStorage.setItem(KEY,JSON.stringify(state))}catch(e){};progress()};
const progress=()=>{document.getElementById('progress').textContent=Object.values(state).filter(s=>s.reviewed).length+' / '+DATA.length+' reviewed'};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const main=document.getElementById('main');
for(const d of DATA){const s=state[d.name]||(state[d.name]={not_pii:[],is_pii:[],missing:'',reviewed:false});
 const sec=document.createElement('section');if(s.reviewed)sec.className='done';
 const row=(v,list,extra)=>`<tr><td>${extra}</td><td class="v">${esc(v)}</td><td><label class="flag"><input type="checkbox" data-list="${list}" data-v="${esc(v)}" ${s[list].includes(v)?'checked':''}> wrong</label></td></tr>`;
 sec.innerHTML=`<h2>${esc(d.name)}</h2><p class="meta">${esc(d.kind)} &middot; ${esc(d.language)} &middot; ${d.pages} page(s)</p><div class="grid"><div class="pages">${d.images.map(i=>`<img alt="page" src="data:image/png;base64,${i}">`).join('')}</div><div>
 <h3>Must be masked (${d.expected.length})</h3><table><tr><th>Category</th><th>Value</th><th></th></tr>${d.expected.map(e=>row(e[1],'not_pii',esc(e[0])+(e[2]==='image'?' (image)':''))).join('')}</table>
 <h3>Must stay visible (${d.negatives.length})</h3><table>${d.negatives.map(n=>row(n,'is_pii','')).join('')}</table>
 <h3>Either is fine (${d.optional.length})</h3><p class="meta">${d.optional.map(esc).join(' &middot; ')||'none'}</p>
 <h3>PII on the page that is missing from the key</h3><textarea placeholder="One value per line">${esc(s.missing)}</textarea>
 <label class="ok"><input type="checkbox" class="rev" ${s.reviewed?'checked':''}> I reviewed this document</label></div></div>`;
 sec.addEventListener('change',e=>{const t=e.target;if(t.dataset.list){const l=s[t.dataset.list];const i=l.indexOf(t.dataset.v);if(t.checked&&i<0)l.push(t.dataset.v);if(!t.checked&&i>=0)l.splice(i,1)}
  if(t.classList.contains('rev')){s.reviewed=t.checked;sec.className=t.checked?'done':''}save()});
 sec.querySelector('textarea').addEventListener('input',e=>{s.missing=e.target.value;save()});main.append(sec)}
document.getElementById('save').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(state,null,1)],{type:'application/json'}));a.download='review.json';a.click()};
progress();
</script></body></html>"""


def build(set_dir):
    key = json.loads((set_dir / 'answer_key.json').read_text(encoding='utf-8')); data = []
    for name, spec in key.items():
        if name.startswith('_'): continue
        with pymupdf.open(set_dir / 'pdfs' / f'{name}.pdf') as pdf:
            images = [base64.b64encode(p.get_pixmap(dpi=72).tobytes('png')).decode() for p in pdf]
        data.append({'name': name, 'kind': spec['kind'], 'language': spec['language'], 'pages': spec['pages'], 'images': images,
                     'expected': spec['expected'], 'negatives': spec['negatives'], 'optional': spec['optional']})
    page = PAGE.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    (set_dir / 'review.html').write_text(page, encoding='utf-8'); print('wrote', set_dir / 'review.html', f'({len(page) // 1024} KB)')


def apply(set_dir, review_path):
    """Apply reviews to the ORIGINAL key (kept as answer_key.unreviewed.json), so re-applying is safe.
    review_path holds {doc: review} or a list of {id, data} records from a shared review store."""
    path, original = set_dir / 'answer_key.json', set_dir / 'answer_key.unreviewed.json'
    if not original.exists(): original.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
    key = json.loads(original.read_text(encoding='utf-8'))
    review = json.loads(Path(review_path).read_text(encoding='utf-8'))
    if isinstance(review, dict) and isinstance(review.get('documents'), list): review = review['documents']
    if isinstance(review, list): review = {d['id']: d.get('data', d) for d in review}
    changed = 0
    for name, r in review.items():
        spec = key.get(name)
        if not spec or not r.get('reviewed'): continue
        moved = [e for e in spec['expected'] if e[1] in r.get('not_pii', [])]
        spec['expected'] = [e for e in spec['expected'] if e[1] not in r.get('not_pii', [])]
        spec['optional'] = sorted(set(spec['optional']) | {e[1] for e in moved})
        for v in r.get('is_pii', []):
            if v in spec['negatives']: spec['negatives'].remove(v)
            spec['expected'].append(['unreviewed-category', v, 'text' if spec['variant'] == 'native' else 'image'])
        for v in [line.strip() for line in r.get('missing', '').splitlines() if line.strip()]:
            spec['expected'].append(['unreviewed-category', v, 'text' if spec['variant'] == 'native' else 'image'])
        spec['reviewed'] = True; changed += 1
    key['_meta']['review_status'] = f'{changed} of {len([k for k in key if not k.startswith("_")])} documents reviewed by a person'
    path.write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding='utf-8'); print(key['_meta']['review_status'])


if __name__ == '__main__':
    action, target = sys.argv[1], Path(sys.argv[2])
    {'build': lambda: build(target), 'apply': lambda: apply(target, sys.argv[3])}[action]()
