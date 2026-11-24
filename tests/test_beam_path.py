"""Run the browser optics regression suite in the repository's existing test job."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="Browser optics tests require Node.js")
def test_browser_optics():
    suite = Path(__file__).with_name("beam-path.test.mjs")
    subprocess.run(["node", "--test", str(suite)], check=True)
