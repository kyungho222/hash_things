import unittest

from simhash_matcher.public_simhash import has_hash, format_simhash, make_simhash


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

    def test_has_hash_runs_create_and_compare(self):
        value = make_simhash("제목", "본문 내용")
        db = Connection({format_simhash(value)})
        self.assertIs(has_hash(db, "제목", "본문 내용", table="ASADAL_ce77dc5e9fd4_LEARN_LIST"), True)
        self.assertIs(has_hash(db, "다른 제목", "다른 본문", table="ASADAL_ce77dc5e9fd4_LEARN_LIST"), False)

    def test_missing_payload_logs_warning_and_skips(self):
        with self.assertLogs("simhash_matcher.public_simhash", level="WARNING") as logs:
            result = has_hash(Connection(set()), None, "본문", table="ASADAL_ce77dc5e9fd4_LEARN_LIST")
        self.assertIs(result, False)
        self.assertIn("simhash 생성에 필요한 payload 중 subject 누락", logs.output[0])


if __name__ == "__main__": unittest.main()

