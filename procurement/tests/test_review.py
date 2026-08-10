import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procurement_os.review import ReviewDecision,DecisionScope,writeback_kind
class T(unittest.TestCase):
 def test_run(self): self.assertEqual(writeback_kind(ReviewDecision('SKIP','SKIP',DecisionScope.RUN_ONLY)),'RUN_DECISION')
 def test_temp(self):
  with self.assertRaises(ValueError): writeback_kind(ReviewDecision('PRICE','OK',DecisionScope.TEMPORARY))
 def test_perm(self): self.assertEqual(writeback_kind(ReviewDecision('ALIAS','OK',DecisionScope.PERMANENT)),'POLICY_OR_ALIAS')
if __name__=='__main__': unittest.main()
