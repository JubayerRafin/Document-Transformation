"""Content-free summaries over existing transport and stage evidence."""
import math


def summarize(events):
    rows=[r for r in events if r.get('event')=='transport_completed']
    if not rows:return {'transport_calls':0,'timing_available':False}
    seconds=sum(r.get('seconds',0) for r in rows)
    decode=sum(r.get('eval_duration',0) for r in rows)/1e9
    prefill=sum(r.get('prompt_eval_duration',0) for r in rows)/1e9
    outputs=sum(r.get('eval_count',0) for r in rows)
    latencies=sorted(r.get('seconds',0) for r in rows)
    points=[]
    for r in rows:
        if 'time' in r and 'seconds' in r:
            points.extend([(r['time']-r['seconds'],1),(r['time'],-1)])
    active=peak=0
    for _,delta in sorted(points):active+=delta;peak=max(peak,active)
    return {'transport_calls':len(rows),'timing_available':True,'http_seconds_sum':seconds,
            'mean_http_seconds':seconds/len(rows),'p50_http_seconds':latencies[math.ceil(.5*len(rows))-1],
            'p95_http_seconds':latencies[math.ceil(.95*len(rows))-1],
            'max_observed_in_flight':peak,'prompt_tokens':sum(r.get('prompt_eval_count',0) for r in rows),
            'output_tokens':outputs,'decode_seconds':decode,'prefill_seconds':prefill,
            'load_seconds':sum(r.get('load_duration',0) for r in rows)/1e9,
            'decode_tokens_per_second':outputs/decode if decode else None,
            'output_tokens_per_http_second':outputs/seconds if seconds else None,
            'retry_starts':sum(r.get('event')=='request_started' and r.get('attempt',r.get('segment_attempt',1))>1 for r in events),
            'validation_failures':sum(r.get('event')=='validation_failed' for r in events),
            'scope':'Completed transports include retries; summed request time can overlap under concurrency.'}
