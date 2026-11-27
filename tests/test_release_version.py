"""The public release contract must follow the real product version."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from brush_converter import __version__  # noqa: E402
from tools import check_docs  # noqa: E402


def test_documentation_release_contract_tracks_product_version():
    assert check_docs.RELEASED == "v" + __version__
    assert check_docs.CANDIDATE is None
    assert check_docs.check_contract(ROOT) == []
