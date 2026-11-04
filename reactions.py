"""Move the robot in time with the audio tags in the agent's speech.

The agent writes tags such as ``[laughs]`` or ``[excited]`` for its voice
(Eleven v3). Each ``audio`` event carries per-character timing for the text it
speaks, tags included, so the app can show a face and play a short gesture at
the moment the voice reaches the tag, with no tool call.

A tag can be split across two audio events (``[laughs`` then ``]``), so the
parser keeps its state between events.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from palmimo_sdk.agent import AgentToolSet

logger = logging.getLogger("elevenlabs_agent")

#: How long a reaction's face stays before the display goes back to neutral.
FACE_HOLD_SECS = 2.5
#: Length of a reaction's gesture; short so it fits the words it goes with.
GESTURE_SECS = 1.5
#: The same gesture is not repeated within this long (a clap on every reply gets tiresome).
GESTURE_REPEAT_SECS = 20.0
#: Added to each reaction's time, to line it up with the speaker's own buffering.
SPEAKER_LATENCY_SECS = 0.0
#: Longest text between brackets still read as a tag.
MAX_TAG_CHARS = 40


@dataclass(frozen=True)
class Reaction:
    face: str
    gesture: str | None = None


#: Words in a tag -> reaction. The first entry with a word found in the tag wins.
REACTIONS: tuple[tuple[tuple[str, ...], Reaction], ...] = (
    (("laugh", "chuckle", "giggle"), Reaction("HAPPY", "body_tilt")),
    (("excited", "cheer", "woo"), Reaction("EXCITED", "clap")),
    (("surprise", "gasp", "amazed", "shock"), Reaction("SURPRISE")),
    (("curious", "question", "wonder"), Reaction("CURIOUS", "body_tilt")),
    (("thought", "think", "hmm", "ponder"), Reaction("THINKING")),
    (("whisper", "shy", "sheepish"), Reaction("SHY")),
    (("angry", "annoyed", "frustrat"), Reaction("ANGRY", "head_shake")),
    (("sigh", "sad", "sympath", "sorry"), Reaction("SAD")),
    (("warm", "happy", "delight", "reassur", "cheerful", "proud"), Reaction("LOVE", "nod")),
)

#: Added to the agent's prompt, so it knows which tags also move the robot.
PROMPT_LINES = (
    (
        "Your audio tags also move the robot as you say them: [laughs], [excited], [surprised], [curious], "
        "[thoughtful], [whispers], [annoyed], [sighs] and [warmly] each show a matching face and gesture, "
        "so you need no tool call for those."
    ),
    (
        "Use at most one audio tag per reply, and only when you really feel it; most replies need none. "
        "Do not use [excited] for ordinary answers or confirmations; save it for good news."
    ),
)


def match(tag: str) -> Reaction | None:
    """The reaction for the text inside a tag, or None if no word matches."""
    tag = tag.lower()
    for words, reaction in REACTIONS:
        if any(word in tag for word in words):
            return reaction
    return None


class TagReactor:
    """Finds tags in the agent's audio events and plays each one's reaction on time."""

    def __init__(
        self, robot: Any, toolset: AgentToolSet, resting_face: Callable[[], str | None] = lambda: None
    ) -> None:
        self._robot = robot
        self._toolset = toolset
        #: The face to go back to after a reaction (e.g. THINKING while Codex works); None for neutral.
        self._resting_face = resting_face
        self._latest: object = None  # the newest reaction; only it restores the resting face
        self._gestured_at: dict[str, float] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._tag: list[str] | None = None  # text of a tag still open; None outside a tag
        self._tag_at = 0.0  # when the open tag's "[" is spoken (time.monotonic())

    def feed(self, alignment: dict[str, Any] | None, chunk_starts_at: float) -> None:
        """Read one audio event's *alignment*; *chunk_starts_at* is when its audio starts playing."""
        if not alignment:
            return
        for char, start_ms in zip(alignment.get("chars") or [], alignment.get("char_start_times_ms") or []):
            if char == "[":
                self._tag = []
                self._tag_at = chunk_starts_at + start_ms / 1000
            elif self._tag is None:
                continue
            elif char == "]":
                self._schedule("".join(self._tag).strip(), self._tag_at)
                self._tag = None
            elif len(self._tag) < MAX_TAG_CHARS:
                self._tag.append(char)
            else:
                self._tag = None  # a stray "[" in ordinary text

    def _schedule(self, tag: str, at: float) -> None:
        reaction = match(tag)
        if reaction is None:
            logger.info("tag [%s]: no reaction", tag)
            return
        task = asyncio.create_task(self._react(tag, reaction, at + SPEAKER_LATENCY_SECS))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _react(self, tag: str, reaction: Reaction, at: float) -> None:
        await asyncio.sleep(max(0.0, at - time.monotonic()))
        # Skip the gesture while another motion runs (the agent's own tool call, or the last reaction),
        # or when it was just played.
        now = time.monotonic()
        gesture = reaction.gesture
        if gesture and (self._toolset.is_busy() or now - self._gestured_at.get(gesture, -1e9) < GESTURE_REPEAT_SECS):
            gesture = None
        if gesture:
            self._gestured_at[gesture] = now
        logger.info("tag [%s]: face %s, gesture %s", tag, reaction.face, gesture or "none")
        token = self._latest = object()
        if self._robot.display is not None:
            if self._resting_face() is None:
                # A hold time makes the display go back to neutral by itself.
                await self._set_face(reaction.face, int(FACE_HOLD_SECS * 1000))
            else:
                # The display can only fall back to neutral, so go back to the resting face here instead.
                await self._set_face(reaction.face)
                asyncio.get_running_loop().call_later(FACE_HOLD_SECS, self._restore, token)
        if gesture:
            await self._toolset.call(gesture, {"seconds": GESTURE_SECS})

    def _restore(self, token: object) -> None:
        if token is not self._latest:
            return  # a newer reaction is showing; it restores the face when it ends
        task = asyncio.create_task(self._set_face(self._resting_face()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _set_face(self, face: str | None, hold_ms: int = 0) -> None:
        try:
            if face is None:
                await asyncio.to_thread(self._robot.display.idle)
            else:
                await asyncio.to_thread(self._robot.set_expression, face, hold_ms)
        except Exception as exc:  # noqa: BLE001 -- the face is decoration; never let it stop the voice
            logger.warning("could not change the face: %s", exc)

    def interrupt(self) -> None:
        """Drop reactions not played yet, e.g. when the user talks over the robot."""
        self._tag = None
        for task in self._tasks:
            task.cancel()

    async def close(self) -> None:
        self.interrupt()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
