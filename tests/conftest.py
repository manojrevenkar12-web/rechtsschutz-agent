import sys
from pathlib import Path
import pytest, yaml
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))

def _load(split):
    return yaml.safe_load((ROOT / "data" / "golden" / f"{split}.yaml").read_text(encoding="utf-8"))["cases"]

@pytest.fixture(scope="session")
def test_cases():
    return _load("test")

@pytest.fixture(scope="session")
def all_cases():
    return _load("test") + _load("dev")
