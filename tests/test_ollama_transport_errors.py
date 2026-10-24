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


if __name__=='__main__':unittest.main()
