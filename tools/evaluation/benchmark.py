"""Explicit GPU first/repeat benchmark in one process; never rents a pod."""
import argparse
import json
from pathlib import Path
from tools.evaluation.dataset import run,save,gpu_state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first',type=Path,required=True)
    parser.add_argument('--repeat',type=Path,required=True)
    parser.add_argument('--session',type=Path,required=True)
    args=parser.parse_args()
    configs=[json.loads(p.read_text(encoding='utf-8')) for p in (args.first,args.repeat)]
    excluded={'output_root','benchmark_label','benchmark_note'}
    policies=[{k:v for k,v in c.items() if k not in excluded} for c in configs]
    if policies[0]!=policies[1]:
        raise ValueError('Both passes must use identical settings apart from labels and output roots.')
    roots=[Path(c['output_root']).resolve() for c in configs]
    if roots[0]==roots[1] or any((p/'translation/index.json').exists() for p in roots):
        raise ValueError('Use separate experiment roots with no prior translation batch.')
    cache=Path(configs[0]['ocr_cache_root'])
    if configs[0].get('ocr_cache_mode')!='reuse' or any(cache.rglob('complete.json')):
        raise ValueError('Use a new shared OCR cache directory for the first pass; no cache is deleted automatically.')
    if args.session.exists():
        raise ValueError('Benchmark session exists; preserve it and choose a new path.')
    session={'method':'Two sequential passes in the same Python process; no automatic model unload.',
        'limits':'First pass starts with an unused OCR cache. Model coldness is not assumed; initial residency and Ollama load durations are evidence.',
        'passes':[]}
    for config in configs:
        before=gpu_state(config['endpoint'])
        session['passes'].append({'label':config.get('benchmark_label'),'root':config['output_root'],'initial_model_residency':before,'status':'running'})
        save(args.session,session)
        try:
            run(config,'translation')
        except BaseException:
            session['passes'][-1]['status']='interrupted_or_failed';save(args.session,session)
            raise
        session['passes'][-1]['status']='finished';save(args.session,session)
    print('Finished both benchmark passes. Compare quality as well as time; no automatic approval.')


if __name__=='__main__':main()
