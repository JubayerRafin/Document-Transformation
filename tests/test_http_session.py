import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from transformation.llm import http_session


class SessionTests(unittest.TestCase):
    def test_real_http11_connection_reuses_socket(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        clients=[]
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_POST(self):
                clients.append(self.client_address)
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                body=json.dumps(payload).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(body)))
                self.end_headers(); self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            url=f'http://127.0.0.1:{server.server_port}/api/chat'
            self.assertEqual(http_session.post_json(url,{'n':1}),{'n':1})
            self.assertEqual(http_session.post_json(url,{'n':2}),{'n':2})
            self.assertEqual(len(clients),2)
            self.assertEqual(clients[0],clients[1])
        finally:
            http_session.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)

    def tearDown(self):
        http_session.close()

    def response(self, body=b'{"ok":true}', status=200, will_close=False):
        response=MagicMock(status=status,reason='fixture',headers={},will_close=will_close)
        response.read.return_value=body
        return response

    def test_same_endpoint_reuses_connection_and_preserves_each_payload(self):
        with patch('http.client.HTTPConnection') as factory:
            connection=factory.return_value
            connection.getresponse.side_effect=[self.response(),self.response()]
            for value in ('first','second'):
                self.assertEqual(http_session.post_json('http://127.0.0.1:1234/api/chat',{'text':value}),{'ok':True})
            factory.assert_called_once()
            self.assertEqual(connection.request.call_count,2)
            self.assertEqual([json.loads(c.kwargs['body']) for c in connection.request.call_args_list],
                             [{'text':'first'},{'text':'second'}])

    def test_endpoint_change_closes_old_connection(self):
        with patch('http.client.HTTPConnection') as factory:
            factory.return_value.getresponse.side_effect=[self.response(),self.response()]
            http_session.post_json('http://localhost:1234/api/chat',{})
            http_session.post_json('http://localhost:5678/api/chat',{})
            self.assertEqual(factory.call_count,2)
            factory.return_value.close.assert_called_once()

    def test_failure_is_not_replayed_and_next_attempt_gets_new_connection(self):
        with patch('http.client.HTTPConnection') as factory:
            connection=factory.return_value
            connection.getresponse.side_effect=[ConnectionResetError('closed'),self.response()]
            with self.assertRaises(ConnectionResetError):
                http_session.post_json('http://localhost:1234/api/chat',{})
            self.assertEqual(connection.request.call_count,1)
            http_session.post_json('http://localhost:1234/api/chat',{})
            self.assertEqual(factory.call_count,2)

    def test_http_error_retains_body_and_closes_socket(self):
        with patch('http.client.HTTPConnection') as factory:
            factory.return_value.getresponse.return_value=self.response(b'{"error":"missing model"}',404)
            with self.assertRaises(HTTPError) as caught:
                http_session.post_json('http://localhost:1234/api/chat',{})
            self.assertIn(b'missing model',caught.exception.read())
            factory.return_value.close.assert_called_once()

    def test_server_close_and_bad_json_discard_connection(self):
        with patch('http.client.HTTPConnection') as factory:
            factory.return_value.getresponse.side_effect=[self.response(will_close=True),self.response(b'bad')]
            http_session.post_json('http://localhost:1234/api/chat',{})
            with self.assertRaises(ValueError):http_session.post_json('http://localhost:1234/api/chat',{})
            self.assertEqual(factory.call_count,2)

    def test_rejects_external_hosts_and_url_credentials(self):
        with patch('http.client.HTTPConnection') as factory:
            for url in ('http://192.0.2.1/api/chat','http://user:pass@localhost/api/chat'):
                with self.assertRaises(ValueError):http_session.post_json(url,{})
            factory.assert_not_called()

    def test_opt_in_chat_keeps_validation_and_usage(self):
        from transformation.llm.ollama_native import chat
        result={'done':True,'message':{'content':'translated'},'eval_count':3}
        with patch.dict('os.environ',{'TRANSFORMX_HTTP_KEEPALIVE':'1'}),\
             patch('transformation.llm.http_session.post_json',return_value=result) as send:
            text,stats=chat('http://localhost:1234/v1','translategemma:12b',[{'role':'user','content':'source'}])
            self.assertEqual((text,stats['eval_count']),('translated',3))
            payload=send.call_args.args[1]
            self.assertEqual(payload['messages'],[{'role':'user','content':'source'}])
            self.assertEqual(payload['options']['temperature'],0)
        with patch.dict('os.environ',{'TRANSFORMX_HTTP_KEEPALIVE':'1'}),\
             patch('transformation.llm.http_session.post_json',return_value={'done':False,'message':{'content':'partial'}}):
            with self.assertRaisesRegex(ValueError,'incomplete'):chat('http://localhost:1234','test',[])
