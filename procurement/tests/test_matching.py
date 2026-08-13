import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from procurement_os.matching import MatchCandidate,score_candidate,normalize_text
class T(unittest.TestCase):
 def test_norm(self): self.assertIn('SB',normalize_text('Josh Sauvignon Blanc 750ml'))
 def test_size_conflict(self):
  r=score_candidate(MatchCandidate('Black Velvet 1.75L','Black Velvet','1.5L'))
  self.assertTrue(r.blocked)
 def test_close_title(self):
  r=score_candidate(MatchCandidate('JOSH CELLARS SB 750ML','Josh Cellars Sauvignon Blanc','750ML'),.80,.70)
  self.assertFalse(r.auto_match)
  self.assertTrue(r.review)
  self.assertIn('FUZZY_SUPPORTING_EVIDENCE_ONLY',r.reasons)
if __name__=='__main__': unittest.main()
