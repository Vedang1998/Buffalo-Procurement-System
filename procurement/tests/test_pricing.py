import sys,unittest
from pathlib import Path
from datetime import date
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procurement_os.pricing import rollover,validate_rollover_date
class T(unittest.TestCase):
 def test_day(self):
  with self.assertRaises(ValueError): validate_rollover_date(date(2026,9,2),[date(2026,9,1)],0)
 def test_unverified(self):
  with self.assertRaises(ValueError): validate_rollover_date(date(2026,9,1),[date(2026,9,1)],1)
 def test_valid(self): self.assertEqual(validate_rollover_date(date(2026,9,1),[date(2026,9,1)],0),date(2026,9,1))
 def test_operational_rollover_is_disabled_until_canonical_guards_exist(self):
  with self.assertRaises(RuntimeError): rollover(object(),date(2026,9,1))
if __name__=='__main__': unittest.main()
