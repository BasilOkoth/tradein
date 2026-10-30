import unittest
from v1_gate import transition
class GateTests(unittest.TestCase):
    def test_wait_trigger(self):
        self.assertEqual(transition('WAIT_HIGH',0,11,15),('WAIT_HIGH',0,False))
        self.assertEqual(transition('WAIT_HIGH',0,15,15),('ACTIVE',0,True))
    def test_cap_rearm(self):
        self.assertEqual(transition('ACTIVE',1,12,15),('ACTIVE',1,True))
        self.assertEqual(transition('WAIT_LOW',2,17,15),('WAIT_LOW',2,False))
        self.assertEqual(transition('WAIT_LOW',2,14,15),('WAIT_HIGH',0,False))
        self.assertEqual(transition('WAIT_HIGH',0,16,15),('ACTIVE',0,True))
if __name__=='__main__':unittest.main()
