import asyncio
import unittest
from types import SimpleNamespace

from skills import SkillRegistry
from skills.sleep import parse_command


class BodyTools:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def cancel_running(self):
        self.calls.append(("cancel", {}))

    async def call(self, name, params):
        self.calls.append((name, params))
        await asyncio.sleep(0)
        return SimpleNamespace(is_error=name == self.fail, text="motion failed")


class ParserTests(unittest.TestCase):
    def test_commands(self):
        for text in ("パルミーモに寝て", " パルミーモ、寝て。", "パルミーモ に 寝て！"):
            self.assertEqual(parse_command(text), "sleep")

    def test_non_commands(self):
        for text in ("パルミーモに寝てと言った", "パルミーモ寝てないで", "寝て", "こんにちは"):
            self.assertIsNone(parse_command(text))


class SkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_and_tool_share_one_animation(self):
        body = BodyTools()
        registry = SkillRegistry(body)
        self.assertFalse(registry.feed("こんにちは"))
        self.assertTrue(registry.feed("パルミーモに寝て"))
        await registry.sleep._task
        result = await registry.handlers["palmimo_sleep"]({})
        self.assertFalse(result[1])
        self.assertEqual([name for name, _ in body.calls], ["cancel", "set_face", "look", "nod", "sleep"])
        self.assertEqual(body.calls[1][1], {"name": "SLEEPY"})
        self.assertTrue(registry.sleep.sleeping)
        await registry.close()

    async def test_failure_stops_sequence_and_allows_retry(self):
        body = BodyTools(fail="look")
        registry = SkillRegistry(body)
        self.assertTrue((await registry.sleep.run({}))[1])
        self.assertFalse(registry.sleep.sleeping)
        self.assertNotIn("sleep", [name for name, _ in body.calls])
        body.fail = None
        self.assertFalse((await registry.sleep.run({}))[1])

    async def test_close_cancels_animation(self):
        body = BodyTools()
        registry = SkillRegistry(body)
        registry.feed("パルミーモに寝て")
        await asyncio.sleep(0)
        await registry.close()
        self.assertFalse(registry.sleep.sleeping)


if __name__ == "__main__":
    unittest.main()
