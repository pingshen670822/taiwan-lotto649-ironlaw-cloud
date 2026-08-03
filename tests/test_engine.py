import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine

class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.draws=engine.load_draws()
    def test_history_integrity(self):
        self.assertGreaterEqual(len(self.draws),2100)
        self.assertEqual(len({d.period for d in self.draws}),len(self.draws))
    def test_models(self):
        m=engine.model_suite(self.draws[:500])
        self.assertTrue(all(len(x)==49 for x in m.values()))
        self.assertTrue(all(abs(x.sum()-6)<1e-6 for x in m.values()))
    def test_next_draw(self): self.assertEqual(engine.next_draw("2026-07-14"),"2026-07-17")
    def test_sets(self):
        import numpy as np
        sets=engine.build_sets(np.linspace(.05,.2,49))
        self.assertEqual(len(sets),8)
        self.assertTrue(all(len(set(x))==6 for x in sets))
    def test_rank_boundary(self):
        import numpy as np
        score=np.arange(1,50,dtype=float)
        actual=np.zeros(49); actual[[48,39,34,33,30,0]]=1
        self.assertEqual(engine.rank_positions(score,actual),[1,10,15,16,19,49])
    def test_top9_walk_forward_contract(self):
        bt=engine.walk_forward(self.draws,30,False)
        self.assertEqual(bt["rank_cutoff"],9)
        self.assertEqual(bt["spill_range"],[10,15])
        self.assertEqual(len(bt["rows"]),30)
        self.assertEqual(len(bt["recent_rank_audit"]),20)
        self.assertTrue(all(len(x["actual_ranks"])==6 for x in bt["recent_rank_audit"]))
        self.assertIn(bt["rank_fusion_share"],engine.RANK_BLEND_CHOICES)
    def test_production_precision_compression(self):
        import numpy as np
        score=np.arange(1,50,dtype=float)
        adjusted,meta=engine.apply_production_policy(score,(41,42,43,44,45,46))
        self.assertEqual(meta["count"],4)
        self.assertEqual(meta["rank_share"],.25)
        self.assertEqual(meta["repeat_cap"],3)
        top=(np.argsort(adjusted)[::-1][:9]+1).tolist()
        self.assertLessEqual(len(set(top)&{41,42,43,44,45,46}),3)
        self.assertNotEqual((np.argsort(score)[::-1][:9]+1).tolist(),(np.argsort(adjusted)[::-1][:9]+1).tolist())
if __name__=="__main__": unittest.main()
