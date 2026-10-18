"""Single-flight TTL cache."""
import threading
import unittest

from src.utils.row_cache import _TTLCache


class TtlCacheTests(unittest.TestCase):
    def test_single_flight_shares_one_factory_call(self):
        cache = _TTLCache()
        calls = {"n": 0}
        hold = threading.Event()
        entered = threading.Event()

        def factory():
            calls["n"] += 1
            entered.set()
            hold.wait(timeout=2)
            return "payload"

        results: list = []

        def worker() -> None:
            results.append(cache.get_or_set("k", 60.0, factory))

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(entered.wait(timeout=2))
        second.start()
        hold.set()
        first.join(timeout=2)
        second.join(timeout=2)

        self.assertEqual(calls["n"], 1)
        self.assertEqual(results, ["payload", "payload"])

    def test_hit_skips_factory(self):
        cache = _TTLCache()
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return 1

        self.assertEqual(cache.get_or_set("k", 60.0, factory), 1)
        self.assertEqual(cache.get_or_set("k", 60.0, factory), 1)
        self.assertEqual(calls["n"], 1)


class ClearReadCachesTests(unittest.TestCase):
    def test_clear_drops_cached_rows(self):
        from src.utils.row_cache import cached_table_rows, clear_read_caches

        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return [{"a": calls["n"]}]

        first = cached_table_rows("delete-bust", 60.0, loader)
        clear_read_caches()
        second = cached_table_rows("delete-bust", 60.0, loader)
        self.assertEqual(first, [{"a": 1}])
        self.assertEqual(second, [{"a": 2}])
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
