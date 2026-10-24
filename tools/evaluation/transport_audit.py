"""Read saved client/server timings and nested figure failures; no inference."""
import argparse
import html
import json
from pathlib import Path


def audit(batch, output):
    index=json.loads((batch/'translation/index.json').read_text(encoding='utf-8'))
    rows=[]
    for record in index['records']:
        run=Path(record['manifest']).parent
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        calls=[json.loads(line) for path in run.rglob('translation-progress.jsonl')
               for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        calls=[c for c in calls if c.get('event')=='transport_completed']
        client=sum(c.get('seconds',0) for c in calls)
        measured=[c for c in calls if isinstance(c.get('total_duration'),(int,float))]
        server=sum(c['total_duration'] for c in measured)/1e9
        comparable_client=sum(c.get('seconds',0) for c in measured)
        figures=[]
        for path in (run/'figure-runs').glob('*/manifest.json'):
            item=json.loads(path.read_text(encoding='utf-8'))
            figures.append({'status':item['status'],'seconds':item['seconds'],
                            'error':item.get('error'),'engine_seconds':item.get('engine_run',{}).get('seconds')})
        rows.append({'document':record['document_id'],'seconds':manifest['seconds'],
            'stage_seconds':record['stage_seconds'],'calls':len(calls),'client_seconds':round(client,3),
            'server_seconds':round(server,3),'client_minus_server_seconds':round(comparable_client-server,3),
            'calls_with_server_timing':len(measured),'figure_runs':figures})
    totals={key:round(sum(r[key] for r in rows),3) for key in
            ('seconds','calls','client_seconds','server_seconds','client_minus_server_seconds')}
    limits=['Client minus server time includes connection, tunnel/network, transfer and JSON handling; it is not a measured network-only latency.',
            'Nested figure calls are already included in the request totals. Stage and request totals overlap; do not add them.',
            'Persistent HTTP connections are a new opt-in experiment. No GPU speedup or unchanged output quality is established by local tests.',
            'Generated main PDFs can still contain preserved or failed figure regions. They require separate quality review.']
    report={'batch':str(batch.resolve()),'totals':totals,'documents':rows,'limitations':limits}
    output.mkdir(parents=True,exist_ok=True)
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    columns=['document','seconds','calls','client_seconds','server_seconds','client_minus_server_seconds']
    body='<h1>Detailed runtime and transport audit</h1><p>Saved evidence; no new inference.</p><table><tr>'
    body+=''.join('<th>'+html.escape(c.replace('_',' '))+'</th>' for c in columns)+'</tr>'
    for row in rows:
        body+='<tr>'+''.join('<td>'+html.escape(str(row[c]))+'</td>' for c in columns)+'</tr>'
    body+='</table><h2>Nested figure work</h2>'
    for row in rows:
        body+='<h3>'+row['document']+'</h3><pre>'+html.escape(json.dumps(row['figure_runs'],indent=2))+'</pre>'
    body+='<h2>Interpretation limits</h2><ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in limits)+'</ul>'
    (output/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:16px system-ui;max-width:1100px;margin:35px auto}td,th{padding:10px;border:1px solid #bbb}table{border-collapse:collapse}pre{white-space:pre-wrap}</style>'+body,encoding='utf-8')
    print(json.dumps(totals,indent=2))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();audit(args.batch,args.output)
