import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.utils.range_parse import parse_page_range


def test_parse_basic():
    assert parse_page_range("1-3,5,10-12") == [1,2,3,5,10,11,12]


def test_parse_all():
    assert parse_page_range("all", total_pages=4) == [1,2,3,4]


def test_parse_bounds():
    # 2-1 should normalize to [1,2]; values outside total_pages are dropped
    assert parse_page_range("0, -1, 2-1, 100", total_pages=5) == [1, 2]
