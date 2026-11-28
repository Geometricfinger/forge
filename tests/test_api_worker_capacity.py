"""Exercise the real bounded API worker with generated source used only as data."""
from pathlib import Path
import tempfile
import unittest

from forge_core.engine import Analyzer
from forge_core.workspace import initialize


class ApiWorkerCapacity(unittest.TestCase):
    def test_8192_empty_functions_complete_under_production_worker_limits(self):
        source=''.join(f'def unit_{i:04d}():\n pass\n' for i in range(8192)).encode()
        profile={'schema':1,'id':'worker-capacity','title':'Synthetic worker capacity',
                 'targets':[{'api':'hashlib.sha256','capability':'digest'}]}
        with tempfile.TemporaryDirectory() as directory:
            workbench=initialize(Path(directory)/'home')
            result=Analyzer(workbench.home).scan(source,'synthetic:worker-capacity',profile)
        self.assertEqual(result['functions_inspected'],8192)
        self.assertEqual(result['findings'],[])
        self.assertFalse(result['upstream_code_executed'])


if __name__=='__main__':
    unittest.main()
