import unittest
from simhash_matcher.simhash_matcher import format_simhash, has_simhash_match, make_simhash


class Cursor:
    def __init__(self, values): self.values = values
    def execute(self, query, params): self.value = params[0]
    def fetchone(self): return (1,) if self.value in self.values else None
    def close(self): pass


class Connection:
    def __init__(self, values): self.values = values
    def cursor(self): return Cursor(self.values)


class SimhashMatcherTests(unittest.TestCase):
    def test_same_input_generates_same_hash(self):
        self.assertEqual(make_simhash("제목", "본문 내용"), make_simhash("제목", "본문 내용"))

    def test_boolean_match_only(self):
        value = make_simhash("제목", "본문 내용")
        db = Connection({format_simhash(value)})
        self.assertIs(has_simhash_match(db, value, table="crawled_pages"), True)
        self.assertIs(has_simhash_match(db, value + 1, table="crawled_pages"), False)


if __name__ == "__main__": unittest.main()
