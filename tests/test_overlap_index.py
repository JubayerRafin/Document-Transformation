import random
import unittest
from unittest.mock import patch
import pymupdf
from transformation.validation.overlap_index import candidate_pairs
from transformation.validation.output_validation import inspect_output


def brute(rectangles):
    return [(i,j) for i in range(len(rectangles)) for j in range(i+1,len(rectangles))]


class OverlapIndexTests(unittest.TestCase):
    def accepted(self,rects,pairs):
        return [(i,j) for i,j in pairs if (rects[i]&rects[j]).get_area()>min(rects[i].get_area(),rects[j].get_area())*.35]

    def test_overlaps_nested_duplicates_touching_and_degenerate(self):
        rects=list(map(pymupdf.Rect,[(0,0,10,10),(10,0,20,10),(0,0,10,10),(2,2,5,5),
                                   (100,100,120,120),(0,0,0,5),(5,5,2,2)]))
        self.assertEqual(self.accepted(rects,candidate_pairs(rects)),self.accepted(rects,brute(rects)))

    def test_randomized_exact_threshold_parity_and_order(self):
        rng=random.Random(47)
        for _ in range(40):
            rects=[]
            for _ in range(80):
                x,y=rng.uniform(-50,200),rng.uniform(-50,200)
                rects.append(pymupdf.Rect(x,y,x+rng.uniform(0,50),y+rng.uniform(0,50)))
            self.assertEqual(self.accepted(rects,candidate_pairs(rects)),self.accepted(rects,brute(rects)))

    def test_disjoint_lines_are_filtered(self):
        rects=[pymupdf.Rect(0,i*20,100,i*20+10) for i in range(1000)]
        self.assertEqual(candidate_pairs(rects),[])

    def test_exact_threshold_is_strictly_greater_not_greater_or_equal(self):
        rects=[pymupdf.Rect(0,0,100,100),pymupdf.Rect(65,0,165,100),pymupdf.Rect(64.9,0,164.9,100)]
        matches=self.accepted(rects,candidate_pairs(rects))
        self.assertNotIn((0,1),matches)
        self.assertIn((0,2),matches)

    def test_full_validation_report_is_identical(self):
        with pymupdf.open() as pdf:
            page=pdf.new_page()
            for point,text in [((30,30),'text'),((31,30),'overlap'),((30,90),'separate')]:page.insert_text(point,text)
            data=pdf.tobytes()
        indexed=inspect_output(data,'pdf',values=['text'],min_font_size=12)
        with patch('transformation.validation.overlap_index.candidate_pairs',side_effect=brute):
            exhaustive=inspect_output(data,'pdf',values=['text'],min_font_size=12)
        self.assertEqual(indexed,exhaustive)
