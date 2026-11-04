"""Local voice skills, exposed through the app's client-tool system."""

from .sleep import SleepSkill, parse_command

PROMPT_LINES = (
    'When the user says 「パルミーモに寝て」 or 「パルミーモ、寝て」, '
    'call palmimo_sleep, say only a short goodnight, and use no other motion or audio tags. '
    'The app also detects this command locally; calling the tool again is harmless. '
    'This is a sleep pose, not a shutdown of the conversation.',
)


def tool_configs() -> list[dict]:
    return [{
        "type": "client",
        "name": "palmimo_sleep",
        "description": "Yawn with a SLEEPY face, then settle Palmimo into its sleep pose.",
        "expects_response": True,
        "response_timeout_secs": 25,
    }]


class SkillRegistry:
    """Route recognized transcripts and agent tool calls to the same skill."""

    def __init__(self, toolset):
        self.sleep = SleepSkill(toolset)

    @property
    def handlers(self):
        return {"palmimo_sleep": self.sleep.run}

    def feed(self, text: str) -> bool:
        if parse_command(text) != "sleep":
            return False
        self.sleep.start()
        return True

    async def close(self) -> None:
        await self.sleep.close()
