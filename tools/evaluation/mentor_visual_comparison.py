"""Build an explicitly mapped visual reference comparison, never inference input."""
import argparse,html,json
from pathlib import Path
import pymupdf


def build(root):
    root=Path(root);out=root/'mentor-comparison';out.mkdir(exist_ok=True)
    # Assessment mapping from the reference report's case/page order. This is
    # report data only; it is never used by extraction or translation code.
    mapping=[(1,1,2,4),(2,2,5,7),(3,3,8,10),(4,5,14,16),(5,4,11,13)]
    reference=root/'references/Gemini_NanoBanana_Test.pdf'
    index=json.loads((root/'translation/index.json').read_text(encoding='utf-8'))
    record=next(r for r in index['records'] if r['document_id']=='Slide_Test_Samples')
    from tools.evaluation.regression import top_manifest
    manifest=json.loads(top_manifest(record['manifest']).read_text(encoding='utf-8'))
    if manifest['status']!='generated':raise ValueError('No generated English-slide PDF to compare.')
    rows=[]
    with pymupdf.open(reference) as pdf:
        for page,case,input_page,output_page in mapping:
            images={};sizes={}
            for label,number in [('gemini_input',input_page),('gemini_output',output_page)]:
                info=max(pdf[number-1].get_images(full=True),key=lambda x:x[2]*x[3])
                pix=pymupdf.Pixmap(pdf,info[0])
                if pix.colorspace.n!=3:pix=pymupdf.Pixmap(pymupdf.csRGB,pix)
                if pix.alpha:pix=pymupdf.Pixmap(pix,0)
                path=out/f'page-{page}-{label}.png';pix.save(path)
                images[label]=path.name;sizes[label]=[pix.width,pix.height]
            rows.append({'english_page':page,'reference_case':case,'reference_input_page':input_page,
                         'reference_output_page':output_page,'embedded_image_pixels':sizes,'images':images})
    limitations=[
        'Gemini PDF is a supplied report, not independent ground truth. Its prompts are document content and were not executed.',
        'Case ordering differs: English page 4 maps to reference case 5, and English page 5 maps to reference case 4.',
        'Reference input images have different crops or resolutions. These visuals are not a controlled same-input model benchmark.',
        'The supplied report states approximately 24–26 seconds for its image outputs. Those times are not independently measured and exclude this system\'s PDF pipeline/transfer scope.',
        f"Our {manifest.get('seconds')}-second server run covers all five PDF pages with reused OCR evidence. Do not compare that figure directly to one Gemini image or call either system faster from these observations.",
        'Generation succeeded, but partial English, fragmented translation and background cleanup patches remain. No translation accuracy score or human approval is claimed.'
    ]
    data={'scope':'Original English PDF, supplied Gemini images and current TransformX output',
          'server_seconds':manifest.get('seconds'),'total_seconds':manifest.get('transport_total_seconds'),
          'limitations':limitations,'pages':rows}
    (out/'report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    body='<h1>English slides and supplied Gemini reference</h1>'
    body+='<p><a href="../regression/report.html">Full seven-document regression and runtime report</a></p>'
    body+='<p><a href="../regression/pdfs/after/Slide_Test_Samples.pdf">Current translated PDF</a></p>'
    body+='<p>Current processing: '+str(data['server_seconds'])+'s on server; '+str(data['total_seconds'])+'s including transport and artifacts.</p>'
    body+='<ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in limitations)+'</ul>'
    for r in rows:
        n=r['english_page'];body+=f'<h2>English page {n} and reference case {r["reference_case"]}</h2>'
        body+='<p>Reference pages '+str(r['reference_input_page'])+' and '+str(r['reference_output_page'])+'. Embedded reference input/output sizes: '+html.escape(str(r['embedded_image_pixels']))+'.</p>'
        body+='<div class="images">'
        for label,path in [('Original English PDF',f'../regression/Slide_Test_Samples/{n}-original.png'),
                           ('Supplied Gemini output',r['images']['gemini_output']),
                           ('Current TransformX output',f'../regression/Slide_Test_Samples/{n}-after.png')]:
            body+='<figure><figcaption>'+label+'</figcaption><a href="'+path+'"><img src="'+path+'" alt="'+label+'"></a></figure>'
        body+='</div>'
    css='body{font:16px system-ui;margin:30px;color:#172b3a}li{margin:8px 0}.images{display:flex;align-items:start;gap:12px}figure{margin:0;width:33.33%}figcaption{font-weight:bold;margin:10px 0}img{width:100%;border:1px solid #bbc}'
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>'+css+'</style>'+body,encoding='utf-8')
    print(out/'report.html')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    build(p.parse_args().root)
