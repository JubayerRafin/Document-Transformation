import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import unittest
from unittest.mock import patch
from multiprocessing.connection import Listener

from tests.temp_workspace import temporary_workspace
from transformation.pdf.layout_service import install, request, ENV, KEY


class LayoutServiceTests(unittest.TestCase):
    def test_real_loopback_preserves_payload_and_failure(self):
        import numpy as np
        key = secrets.token_hex(32)
        with Listener(('127.0.0.1', 0), authkey=bytes.fromhex(key)) as server:
            def handle():
                for ok in (True, False):
                    with server.accept() as connection:
                        operation, payload = connection.recv()
                        connection.send((ok, payload if ok else 'checksum mismatch'))
            thread = threading.Thread(target=handle, daemon=True)
            thread.start()
            data = np.arange(24,dtype=np.float32).reshape(2,3,4)
            output = request(server.address,key,'run',data)
            self.assertTrue(np.array_equal(data, output))
            with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                request(server.address,key,'metadata','different asset')
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_adapter_keeps_model_identity_and_prediction_contract(self):
        class Model:
            def __init__(self,path):
                raise AssertionError('Local initialization must not occur')
        with temporary_workspace() as root:
            path=root/'model.onnx';path.write_bytes(b'unseen model')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            def remote(address,key,operation,payload):
                if operation=='metadata':
                    self.assertEqual(payload,digest)
                    return {'stride':32,'names':{0:'text'}}
                self.assertEqual(payload, (None, {'images':[1,2,3]}))
                return [[7,8]]
            with patch.dict(os.environ,{ENV:json.dumps(['127.0.0.1',1234]),KEY:'ab'*32}), patch(
                    'transformation.pdf.layout_service.request',side_effect=remote):
                self.assertTrue(install(Model))
                self.assertTrue(install(Model))
                model=Model(path)
                self.assertEqual(model.model.run(None,{'images':[1,2,3]}),[[7,8]])
                self.assertEqual(model._stride,32)

    def test_no_service_keeps_default_and_remote_host_rejected(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(install())
        with patch.dict(os.environ,{ENV:'["example.com",1234]',KEY:'ab'*32}):
            with self.assertRaises(ValueError):install()

    def test_start_failure_restores_environment_and_reaps_process(self):
        from transformation.pdf.layout_service import shared_layout
        with temporary_workspace() as root, patch.dict(os.environ,{ENV:'old',KEY:'original'}), patch(
                'transformation.pdf.layout_service.subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value=1
            with self.assertRaisesRegex(RuntimeError,'startup failed'):
                with shared_layout('missing',root):
                    self.fail('Startup must not yield on failure')
            self.assertEqual(os.environ[ENV],'old')
            self.assertEqual(os.environ[KEY],'original')
