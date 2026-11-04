---
name: palmimo-sleep
description: Make Palmimo yawn and settle into a sleep pose when the user says パルミーモに寝て or パルミーモ、寝て.
---

Use the registered `palmimo_sleep` client tool. It shows SLEEPY, lifts the
head for a yawn, nods drowsily, and uses the SDK sleep body animation.
The transcript parser also starts this action automatically; duplicate calls
are idempotent during the conversation. Keep any spoken reply to a brief
goodnight, without additional gestures or audio tags.

This skill parks the body; it leaves the app and conversation running.
Implementation and routing live in `skills/sleep.py` and `skills/__init__.py`.
