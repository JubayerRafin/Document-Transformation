"""Serial Paddle model reuse; predictor settings and recognition policy are unchanged."""
import json,time


class ModelPool:
    def __init__(self,loader=None):
        self.loader=loader;self.models={};self.loads=0;self.hits=0;self.load_seconds=0

    def get(self,kind,options):
        key=(kind,json.dumps(options,sort_keys=True))
        if key in self.models:
            self.hits+=1;return self.models[key]
        if self.loader is None:
            from paddleocr import TextDetection,TextRecognition
            constructor=TextDetection if kind=='detection' else TextRecognition
        else:constructor=lambda **kw:self.loader(kind,kw)
        started=time.monotonic()
        model=constructor(**options)
        self.load_seconds+=time.monotonic()-started
        self.models[key]=model;self.loads+=1
        return model

    def detection(self,**options):return self.get('detection',options)
    def recognition(self,**options):return self.get('recognition',options)
    def metrics(self):
        return {'scope':'cumulative serial OCR session','model_loads':self.loads,
                'model_reuse_hits':self.hits,'model_load_seconds':self.load_seconds,
                'resident_predictors':len(self.models)}
