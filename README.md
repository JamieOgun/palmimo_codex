# elevenlabs-voice

A Palmimo DevKit app. Say "Palmimo" (パルミーモ) to wake the robot and talk with an ElevenLabs agent. The agent can move the robot, change its face, and run coding tasks on your GitHub repos with Codex. The robot goes back to sleep when you tell it to, or after 25 seconds of quiet.

## Setup in the Portal

1. Add this repo in the Portal and install it.
2. Add these secrets under **シークレットと API キー**, then assign them on the app's page under **環境変数の割り当て**:

   | Secret | Needed for |
   |---|---|
   | `ELEVENLABS_API_KEY` | Talking with the agent |
   | `OPENAI_API_KEY` | Hearing the robot's name, and Codex |
   | `GITHUB_TOKEN` | Coding tasks: needs Contents and Pull requests, read and write, on the repos Codex may use |

3. Set the parameters:

   | Parameter | What it is |
   |---|---|
   | `agent_id` | Your ElevenLabs agent's id (from the agent's page URL) |
   | `codex_model` | Codex model; empty for Codex's default |
   | `mic_device` / `speaker_device` | Part of the device name from `arecord -l` / `aplay -l`; empty for the default |
   | `volume` | Output gain; 1.0 is the voice's own level |

On startup the app adds the robot's tools to your agent, so there is nothing to set up on ElevenLabs beyond creating the agent.

## Coding tasks

Ask for a change in one of your repos by name. The robot clones it into `~/code` on the robot, Codex makes the change in its `workspace-write` sandbox, and the robot pushes a new branch and opens a pull request. It tells you when the task is done. Your GitHub token never reaches Codex: pushes go through a separate copy of the repo.

## Files

| File | Role |
|---|---|
| `main.py` | Wake, conversation loop, audio |
| `wake.py` | Hears the robot's name |
| `robot_tools.py` | Movement and face tools for the agent |
| `coding_tools.py` | Coding tools that drive Codex |
| `github_repos.py` | Clones repos and opens pull requests |
| `palmimo.toml` | What the Portal shows and runs |

## Running on a laptop

```bash
uv run python main.py --agent-id <id> --no-robot
```
