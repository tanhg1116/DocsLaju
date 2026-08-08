import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.services.cache import set_cached_markdown, read_memo_markdown, invalidate_markdown


def test_cache_roundtrip():
    key = ("s1", "f1", 1)
    assert read_memo_markdown(key) is None
    set_cached_markdown(key, "hello")
    assert read_memo_markdown(key) == "hello"
    invalidate_markdown(key)
    assert read_memo_markdown(key) is None
