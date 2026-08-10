import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procurement_os.assortment import AssortmentOffer,may_assort
class T(unittest.TestCase):
 def test_same_product(self): self.assertTrue(may_assort(AssortmentOffer('p1'),AssortmentOffer('p1')))
 def test_book_no_wins(self): self.assertFalse(may_assort(AssortmentOffer('p1',False),AssortmentOffer('p1')))
 def test_cross_default_no(self): self.assertFalse(may_assort(AssortmentOffer('p1'),AssortmentOffer('p2')))
 def test_explicit_cross(self):
  a=AssortmentOffer('p1',True,'EXPLICIT_CROSS_PRODUCT','STELLA'); b=AssortmentOffer('p2',True,'EXPLICIT_CROSS_PRODUCT','STELLA')
  self.assertTrue(may_assort(a,b))
if __name__=='__main__': unittest.main()
