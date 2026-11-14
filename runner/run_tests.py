#!/usr/bin/env python3
"""Run the unchanged research-runner contract with portable temp fixtures."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from tools.test_environment import prepare_test_environment


def main():
    prepare_test_environment()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.testsRun and result.wasSuccessful() and not result.skipped else 2


if __name__ == '__main__':
    raise SystemExit(main())
