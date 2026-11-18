import json,unittest,urllib.error
from io import BytesIO
from unittest.mock import patch
from transformation.llm.ollama_native import chat


class ResponseTests(unittest.TestCase):
    def test_missing_content_is_a_clear_error_not_attribute_crash(self):
        for message in (None,{'content':None}, {'content':5}):
            with patch('urllib.request.urlopen',return_value=BytesIO(json.dumps({'message':message}).encode())):
                with self.assertRaisesRegex(ValueError,'no textual message'):chat('http://localhost:11434','test',[])

    def test_model_http_error_includes_server_reason(self):
        error=urllib.error.HTTPError('http://localhost:11434/api/chat',404,'missing',{},BytesIO(b'{"error":"model not found"}'))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(ValueError,'HTTP 404.*model not found'):chat('http://localhost:11434','test',[])

    def test_valid_response_retains_usage(self):
        response={'message':{'content':'text'},'done':True,'eval_count':3}
        with patch('urllib.request.urlopen',return_value=BytesIO(json.dumps(response).encode())):
            text,stats=chat('http://localhost:11434','test',[])
        self.assertEqual((text,stats['eval_count']),('text',3))


class OpenAICompatibleTests(unittest.TestCase):
    ENV={'TRANSFORMX_OPENAI_COMPATIBLE':'http://127.0.0.1:8000'}

    def answer(self,content='{"a":1}',finish='stop'):
        return BytesIO(json.dumps({'choices':[{'message':{'content':content},'finish_reason':finish}],
                                   'usage':{'prompt_tokens':7,'completion_tokens':3}}).encode())

    def test_listed_server_gets_schema_constrained_request_without_thinking(self):
        schema={'type':'object'}
        with patch.dict('os.environ',self.ENV),patch('urllib.request.urlopen',return_value=self.answer()) as call:
            text,stats=chat('http://127.0.0.1:8000/v1','m',[{'role':'user','content':'x'}],max_output=50,response_format=schema)
        request=call.call_args.args[0];body=json.loads(request.data)
        self.assertTrue(request.full_url.endswith('/v1/chat/completions'))
        self.assertEqual((body['temperature'],body['max_tokens']),(0,50))
        self.assertFalse(body['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(body['response_format']['json_schema']['schema'],schema)
        self.assertEqual((text,stats['prompt_eval_count'],stats['eval_count']),('{"a":1}',7,3))

    def test_truncated_or_empty_answer_is_an_error(self):
        for response in (self.answer(finish='length'),self.answer(content='  ')):
            with patch.dict('os.environ',self.ENV),patch('urllib.request.urlopen',return_value=response):
                with self.assertRaisesRegex(ValueError,'truncated'):chat('http://127.0.0.1:8000/v1','m',[])

    def test_unlisted_server_keeps_the_ollama_protocol_and_remote_hosts_stay_refused(self):
        response={'message':{'content':'text'},'done':True}
        with patch.dict('os.environ',self.ENV),patch('urllib.request.urlopen',return_value=BytesIO(json.dumps(response).encode())) as call:
            chat('http://127.0.0.1:11434/v1','m',[])
        self.assertTrue(call.call_args.args[0].full_url.endswith('/api/chat'))
        # A remote server is refused unless listed, and even when listed must use https (unless the
        # operator allows plain http on a trusted network); see transformation/llm/endpoints.py.
        with patch.dict('os.environ',{'TRANSFORMX_OPENAI_COMPATIBLE':''}):
            with self.assertRaisesRegex(ValueError,'not on this machine'):chat('https://example.com:8000/v1','m',[])
        with patch.dict('os.environ',{'TRANSFORMX_OPENAI_COMPATIBLE':'http://example.com:8000'}):
            with self.assertRaisesRegex(ValueError,'must use https'):chat('http://example.com:8000/v1','m',[])


if __name__=='__main__':unittest.main()
