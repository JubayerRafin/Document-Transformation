import unittest
from types import SimpleNamespace as NS
from transformation.layout.panels import group_panels,PANEL_SLOTS


class Panels(unittest.TestCase):
    def fixture(self):
        text='Both sides held talks for several hours;\nincluding National Security Adviser Jonathan\nPowell.'
        blocks=[]
        paragraphs=[]
        for i in range(3):
            left=10+i*200
            blocks.append((left,100,left+150,160,text,0,0))
            for j,line in enumerate(text.splitlines()):
                paragraphs.append(NS(unicode=line,box=NS(x=left,y=390-j*20,x2=left+150,y2=400-j*20),
                    vertical=False,pdf_style=NS(font_size=12),debug_id=f'{i}-{j}',
                    pdf_paragraph_composition=[NS(pdf_formula=None)]))
        source=NS(rect=NS(width=700,height=500),rotation=0,get_text=lambda mode:blocks,
                  get_drawings=lambda:[{'fill':(1,0,0),'rect':NS(x0=10,x1=100,y0=150,y1=180)}])
        return NS(page_number=0,pdf_paragraph=paragraphs),source

    def test_groups_whole_name_and_stops_above_decoration(self):
        page,source=self.fixture()
        fixed,records=group_panels(page,source)
        self.assertEqual(len(records),3)
        self.assertEqual(len(page.pdf_paragraph),3)
        self.assertTrue(all('Jonathan Powell' in t for t in fixed.values()))
        self.assertEqual(records[0]['bbox'][1],353)

    def test_mismatched_text_not_merged(self):
        page,source=self.fixture()
        page.pdf_paragraph[0].unicode='Unrelated content'
        fixed,records=group_panels(page,source)
        self.assertEqual(len(records),2)
        self.assertEqual(len(page.pdf_paragraph),5)

    def test_portrait_not_grouped(self):
        page,source=self.fixture()
        source.rect.width=400
        self.assertEqual(group_panels(page,source),({},[]))
