"""Japanese sleep-command parsing and the robot's yawn/sleep sequence."""

import asyncio
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


def parse_command(text: str) -> str | None:
    # Match the whole utterance so quoted requests and negations don't park the robot.
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\s、,。.!！?？]", "", text)
    return "sleep" if text in {"パルミーモに寝て", "パルミーモ寝て"} else None


class SleepSkill:
    def __init__(self, toolset):
        self._toolset = toolset
        self._lock = asyncio.Lock()
        self._task = None
        self.sleeping = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run({}))

    async def run(self, args: dict) -> tuple[str, bool]:
        async with self._lock:
            if self.sleeping:
                return "Palmimo is already sleeping.", False
            self.sleeping = True
            try:
                await self._toolset.cancel_running()
                for name, params in (
                    ("set_face", {"name": "SLEEPY"}),
                    ("look", {"pitch": -15, "yaw": 0}),
                    ("nod", {"seconds": 1.5, "face": "SLEEPY"}),
                    ("sleep", {}),
                ):
                    result = await self._toolset.call(name, params)
                    if result.is_error:
                        self.sleeping = False
                        logger.warning("sleep skill failed at %s: %s", name, result.text)
                        return result.text, True
                return "Palmimo yawned and settled into its sleep pose.", False
            except asyncio.CancelledError:
                self.sleeping = False
                raise
            except Exception as exc:
                self.sleeping = False
                logger.exception("sleep skill failed")
                return f"Could not put Palmimo to sleep: {exc}", True

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
