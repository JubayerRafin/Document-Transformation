import importlib.util,tempfile,unittest
from pathlib import Path
import pymupdf
from extraction.figures import enrich_figures


@unittest.skipUnless(importlib.util.find_spec('pdfplumber') and importlib.util.find_spec('cv2'),'Deep Doc figure dependencies required')
class FigureTests(unittest.TestCase):
    def test_shaded_table_background_is_not_a_figure_but_inset_artwork_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'shaded.pdf'
            cells=[[30,120,240,190],[240,120,450,190],[30,190,240,400],[240,190,450,400]]
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=500,height=600)
                for box in cells[:2]:page.draw_rect(box,fill=(.1,.3,.5))
                page.insert_text((45,155),'Type',fontsize=12,color=(1,1,1))
                page.insert_text((260,155),'Details',fontsize=12,color=(1,1,1))
                for box in cells:page.draw_rect(box)
                page.draw_rect((70,240,160,340),fill=(.2,.7,.3))
                pdf.save(source)
            ir={'source_sha256':'fixture','assets':[],'units':[{'id':'p1','number':1,'width':500,'height':600,
                'regions':[{'kind':'table','bbox':[30,120,450,400],
                            'metadata':{'cell_records':[{'bbox':b} for b in cells]}}]}]}
            enrich_figures(source,ir,root/'figures')
            self.assertEqual(len(ir['assets']),1)
            self.assertGreater(ir['assets'][0]['bbox'][1],220)
            self.assertLess(ir['assets'][0]['bbox'][2]-ir['assets'][0]['bbox'][0],150)

    def test_separate_columns_and_table_cell_artwork_remain_separate_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.pdf'
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=500,height=600)
                page.draw_rect((30,40,150,150),fill=(.1,.4,.7))
                page.draw_rect((260,40,420,150),fill=(.7,.3,.1))
                page.draw_rect((30,270,450,470))
                page.draw_line((240,270),(240,470))
                page.draw_rect((60,310,160,420),fill=(.2,.5,.2))
                page.draw_rect((280,310,400,420),fill=(.4,.2,.6))
                pdf.save(source)
            records=[{'bbox':[30,270,240,470]},{'bbox':[240,270,450,470]}]
            ir={'source_sha256':'fixture','assets':[],'units':[{'id':'p1','number':1,'width':500,'height':600,
                'regions':[{'kind':'table','bbox':[30,270,450,470],'metadata':{'cell_records':records}}]}]}
            enrich_figures(source,ir,root/'figures')
            self.assertEqual(len(ir['assets']),4)
            for asset in ir['assets']:
                self.assertTrue(Path(asset['path']).is_file())
                self.assertLess(asset['bbox'][2]-asset['bbox'][0],220)
                pix=pymupdf.Pixmap(asset['path'])
                self.assertGreater(len(set(pix.samples)),1)


if __name__=='__main__': unittest.main()
