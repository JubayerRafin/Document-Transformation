"""Build cloud assets without modifying the local FastAPI application or engine."""
from pathlib import Path
import shutil
import json

PROJECT=Path(__file__).resolve().parents[2]
source=PROJECT/'backend/static'
target=PROJECT/'cloud/public'
target.mkdir(parents=True,exist_ok=True)
copy_rules=json.loads((PROJECT/'ui/product-copy.json').read_text(encoding='utf8'))
def product_copy(text):
    for before,after in copy_rules.items():text=text.replace(before,after)
    return text
for name in ('app.css','cover-original.jpg','cover-translated.jpg'):
    shutil.copy2(source/name,target/name)
html=(source/'index.html').read_text(encoding='utf8')
# FastAPI serves these under /assets; Workers publishes public/ at the root.
html=html.replace('/assets/','/')
html=html.replace('<button class="tx-brand" data-action="home">', '<button class="tx-brand" data-action="home"><img class="tx-brand-mark" src="/transformx-mark.svg" width="40" height="40" alt="" aria-hidden="true">')
html=html.replace('</head>', '<link rel="icon" href="/transformx-mark.svg"></head>')
shutil.copy2(PROJECT/'frontend/public/transformx-mark.svg',target/'transformx-mark.svg')
html=html.replace('</head>','<link rel="stylesheet" href="/studio.css"></head>')
html=html.replace('<div class="tx-layout" id="application"',
 (PROJECT/'cloud/ui/studio-welcome.html').read_text(encoding='utf8')+'<div class="tx-layout" id="application"')
nav_start=html.index('<nav class="tx-site-nav">')
nav_end=html.index('</nav>',nav_start)+len('</nav>')
html=html[:nav_start]+'<nav class="tx-site-nav" aria-label="Studio navigation"><a href="/">Product website</a><a href="/workspace">Your workspace</a><span class="tx-studio-chip">STUDIO</span></nav>'+html[nav_end:]
shutil.copy2(PROJECT/'cloud/ui/studio.css',target/'studio.css')
html=html.replace('Username<input id="username"','Email<input type="email" id="username"')
html=html.replace('Saved evidence stays available offline.','Private project workspace')
html=html.replace('<img id="left-preview" alt="Source document page">','<iframe id="left-preview" title="Source document PDF"></iframe>')
html=html.replace('<img id="right-preview" alt="Transformed document page">','<iframe id="right-preview" title="Transformed document PDF"></iframe>')
html=html.replace('Mentors use reviewer accounts. Viewers can browse explicitly shared baselines. Only administrators can configure accounts and baseline imports using the local CLI.',
 'Invited mentors can process their own documents using their registered GPU worker. Viewers have read-only access. Account roles are managed in Supabase.')
html=html.replace('HTTPS tunnel access requires this computer, API and internet connection to remain on. Temporary links change when restarted.',
 'The hosted application is independent of your laptop. New processing requires a connected GPU worker. Saved results are available subject to hosting limits.')
html=html.replace('PDF to PDF for this evaluated V1 route.','PDF to PDF · Live cloud jobs require a connected processing worker.')
worker_ui=(PROJECT/'cloud/ui/workers.html').read_text(encoding='utf8')
html=html.replace('</main></div>',worker_ui+'</main></div>')
html=html.replace('<button data-view="jobs">Documents</button>','<button data-view="jobs">Documents</button><button data-view="workers">GPU servers</button>')
html=html.replace('<button data-view="jobs">My documents</button>','<button data-view="jobs">My documents</button><button data-view="workers">GPU servers</button>')
html=html.replace('<button data-view="jobs">Your documents</button>','<button data-view="jobs">Your documents</button><button data-view="workers">GPU servers</button>')
html=html.replace('<form id="upload-form">','<form id="upload-form"><label class="tx-field">Processing server<select id="gpu-worker" required><option value="">Select your GPU server</option></select></label><button type="button" data-view="workers">Connect a GPU server</button>')
(target/'index.html').write_text(product_copy(html),encoding='utf8')
js=(source/'app.js').read_text(encoding='utf8')
js=js.replace("['admin','reviewer','viewer']","['admin','mentor','viewer']").replace("['admin','customer']","['admin','mentor','customer']").replace("['admin','reviewer']","['admin','mentor']")
start=js.index('function renderPages()')
end=js.index('async function renderReview()',start)
js=js[:start]+'''let pageGeneration=0;
async function renderPages(){
 const generation=++pageGeneration;if(!selected)return;
 const p=Number(q('#page').value)||1,left=artifact('source'),right=artifact('result');
 if(!left||!right)return;
 try{const links=await Promise.all([left,right].map(a=>api('/v1/jobs/'+selected.id+'/artifacts/'+a.id+'/url')));
 if(generation!==pageGeneration)return;
 q('#left-preview').src=links[0].url+'#page='+p;q('#right-preview').src=links[1].url+'#page='+p;
 }catch(e){notice(e.message);}
}
'''+js[end:]
# Cloud reviews never invent a human name from a UUID.
js=js.replace("${escape(r.reviewer)} ·", "Reviewer ${escape(r.reviewer.slice(0,8))} ·")
js=js.replace("q('#baseline-tools').hidden=user?.role!=='admin';", "q('#baseline-tools').hidden=true;")
js=js.replace("q('#review-controls').hidden=", "q('#worker-register-form').hidden=!user||user.role==='viewer';q('#review-controls').hidden=")
js=js.replace("if(v==='review')renderReview();", "if(v==='workers'||v==='new')renderWorkers();if(v==='review')renderReview();")
js=js.replace("const opts={operation:mode,", "const opts={worker_id:q('#gpu-worker').value,operation:mode,")
js=js.replace("user=null;csrf='';jobs=[];selected=null;account();home();", "user=null;csrf='';jobs=[];selected=null;privateWorkerConfig=null;q('#worker-onboarding').hidden=true;account();home();")
js=js.replace("if(screen==='system')renderSystem();}catch", "if(screen==='system')renderSystem();if(screen==='workers'||screen==='new')await renderWorkers();}catch")
js=js.replace('init();setInterval',(PROJECT/'cloud/ui/workers.js').read_text(encoding='utf8')+'\ninit();setInterval')
js=js.replace("init();setInterval", "q('#compare').hidden=true;init();setInterval")
(target/'app.js').write_text(product_copy(js),encoding='utf8')
with (target/'app.css').open('a',encoding='utf8') as f:
    f.write('\n#tx-v1 .tx-compare iframe{width:100%;height:650px;border:0;background:white} @media(max-width:700px){#tx-v1 .tx-compare iframe{height:480px}}\n')
print('Cloud frontend built. Local frontend and processing engine unchanged.')
