"""Run identical protected translation cases on explicitly selected Ollama models."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from transformation.llm.ollama_native import chat, paragraph_messages
from transformation.llm.protected_text import protect_values, restore_values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--models',nargs='+',required=True)
    parser.add_argument('--endpoint',default='http://127.0.0.1:11435/v1')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    cases=json.loads(args.cases.read_text(encoding='utf-8'))
    # Exclusive creation preserves earlier evidence. No PDF work or model downloads.
    with args.output.open('x',encoding='utf-8') as out:
        for case in cases:
            protected,replacements=protect_values(case['text'],case['protected_values'])
            messages=paragraph_messages(protected,'Korean',case['context'])
            prompt_hash=hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
            for model in args.models:
                row={'case':case['id'],'model':model,'source':case['text'],
                     'prompt_sha256':prompt_hash,'review_status':'not_reviewed'}
                start=time.monotonic()
                try:
                    translated,stats=chat(args.endpoint,model,messages)
                    row['raw_response']=translated
                    row['translation']=restore_values(translated,replacements)
                    row['protected_values_status']='passed'
                    row['prompt_tokens']=stats.get('prompt_eval_count')
                    row['output_tokens']=stats.get('eval_count')
                    row['load_seconds']=stats.get('load_duration',0)/1e9
                except Exception as error:
                    row['error']=str(error)
                    row['protected_values_status']='not_passed'
                row['seconds']=round(time.monotonic()-start,2)
                out.write(json.dumps(row,ensure_ascii=False)+'\n')
                out.flush()
                print(f"{case['id']} / {model}: {row['seconds']} seconds; {row['protected_values_status']}",flush=True)


if __name__=='__main__':
    main()
