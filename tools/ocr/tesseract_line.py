"""Tesseract as a line recognizer, for scripts PaddleOCR has no model for (Bengali).

PaddleOCR 3.x ships recognition models for Latin, Korean, Arabic, Devanagari, Cyrillic, Thai, Greek,
Tamil and Telugu, but none for Bengali: the general model read only the digits and Latin parts of a
scanned Bengali page, about half its characters (evaluation/ocr/bench.py, 2026-11-23). Tesseract 5's
LSTM model for Bengali (Apache 2.0, apt package tesseract-ocr-ben) reads the line crops the Paddle
detector found, through the same predict() interface the Paddle recognizers offer.
"""
import os
import shutil
import subprocess

TESSERACT_MODELS = {'bn': 'ben', 'ar': 'ara'}


def available(language):
    code = TESSERACT_MODELS.get(language)
    if not code or not shutil.which('tesseract'):
        return False
    try:
        listed = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=20).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
    return code in listed


class TesseractRecognizer:
    """predict(image) -> [{'rec_text', 'rec_score'}] for one text line (BGR numpy image)."""

    def __init__(self, language):
        # Bengali alone read 88% of a page's characters, with English 99% (codes, amounts, names).
        self.code = TESSERACT_MODELS[language] + '+eng'
        self.cache = {}

    def _run(self, png):
        result = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', self.code, '--psm', '7', 'tsv'],
                                input=png, capture_output=True, timeout=60,
                                env=dict(os.environ, OMP_THREAD_LIMIT='1'))   # one line: threads only add start-up time
        words, confidences = [], []
        for row in result.stdout.decode('utf-8', 'replace').splitlines()[1:]:
            cells = row.split('	')
            if len(cells) < 12 or cells[0] != '5':          # level 5 = word
                continue
            text, conf = cells[11].strip(), float(cells[10])
            if text:
                words.append(text)
                confidences.append(max(0.0, conf) / 100)
        return {'rec_text': ' '.join(words), 'rec_score': min(confidences) if confidences else 0.0}

    @staticmethod
    def _png(image):
        import cv2
        ok, png = cv2.imencode('.png', image)
        if not ok:
            raise ValueError('Could not encode the line crop.')
        return png.tobytes()

    def prefetch(self, images, workers=16):
        """Read many crops at once (one process each, in parallel); predict() then answers from memory.
        A page read one crop at a time took four times as long as with Paddle."""
        from concurrent.futures import ThreadPoolExecutor
        pngs = [p for p in {self._png(i) for i in images} if p not in self.cache]
        with ThreadPoolExecutor(workers) as pool:
            for png, row in zip(pngs, pool.map(self._run, pngs)):
                self.cache[png] = row

    def predict(self, image):
        png = self._png(image)
        if png not in self.cache:
            self.cache[png] = self._run(png)
        return [dict(self.cache[png])]
