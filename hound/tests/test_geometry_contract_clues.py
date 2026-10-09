import unittest
import hound

def findings(src,profile):
    return [f for f in hound.scan_bytes(src.encode(),source_id='fixture')['findings'] if f['profile_id']==profile]

class GeometryContractClues(unittest.TestCase):
    def test_three_columns_guard(self):
        r=findings('def f(x):\n if x.shape[1]!=3: raise ValueError()\n return x\n',hound.DIMENSION)
        self.assertEqual(r[0]['expected_columns'],3)
    def test_two_columns_guard(self):
        r=findings('def f(x):\n if x.ndim!=2 or x.shape[1]!=2: raise ValueError()\n return x\n',hound.DIMENSION)
        self.assertEqual(r[0]['expected_columns'],2)
    def test_reversed_comparison(self):
        r=findings('def f(x):\n if 3!=x.shape[1]: raise ValueError()\n return x\n',hound.DIMENSION)
        self.assertEqual(r[0]['expected_columns'],3)
    def test_unknown_dimension_not_invented(self):
        self.assertFalse(findings('def f(x,n):\n if x.shape[1]!=n: raise ValueError()\n return x\n',hound.DIMENSION))
    def test_no_raise_not_rejection(self):
        self.assertFalse(findings('def f(x):\n if x.shape[1]!=3: return x\n',hound.DIMENSION))
    def test_docstring_is_not_guard(self):
        self.assertFalse(findings('def f(x):\n """3D N by 3"""\n return x\n',hound.DIMENSION))
    def test_other_axis_not_columns(self):
        self.assertFalse(findings('def f(x):\n if x.shape[0]!=3: raise ValueError()\n',hound.DIMENSION))
    def test_unreachable_guard_suppressed(self):
        self.assertFalse(findings('def f(x):\n return x\n if x.shape[1]!=3: raise ValueError()\n',hound.DIMENSION))
    def test_scaled_matrix_expression(self):
        r=findings('def f(factor,r,x):\n y=factor*(r@x.T).T\n return y\n',hound.SCALED_MATRIX)
        self.assertEqual(r[0]['status'],'SCALED_MATRIX_EXPRESSION_OBSERVED')
    def test_matmul_alone_not_scaled(self):
        self.assertFalse(findings('def f(r,x):\n return r@x\n',hound.SCALED_MATRIX))
    def test_multiply_alone_not_matrix(self):
        self.assertFalse(findings('def f(r,x):\n return r*x\n',hound.SCALED_MATRIX))
    def test_no_function_name_assumption(self):
        r=findings('def this_is_rigid(factor,r,x):\n return factor*(r@x)\n',hound.SCALED_MATRIX)
        self.assertTrue(r)
    def test_no_runtime_type_claim(self):
        r=findings('def f(factor,r,x):\n return factor*(r@x)\n',hound.SCALED_MATRIX)
        self.assertFalse(r[0]['runtime_verified'])
