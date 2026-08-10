import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procurement_os.economics import target_cost,qualifying_quantity,gp_per_100_cash
class T(unittest.TestCase):
 def test_target_cost(self): self.assertEqual(str(target_cost(14.99,.33)),'10.04')
 def test_cs(self): self.assertEqual(str(qualifying_quantity(3,'CS',24)),'3')
 def test_bt(self): self.assertEqual(str(qualifying_quantity(3,'BT',6)),'18')
 def test_gp_cash(self): self.assertEqual(str(gp_per_100_cash(6,40)),'15.00')
if __name__=='__main__': unittest.main()
