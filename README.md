<div align="center">

# NexusTG

**An AI-powered Telegram userbot with a private control bot.**

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Gemini](https://img.shields.io/badge/Gemini-AI-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)](https://ai.google.dev/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-AGPL--3.0-663366?style=flat-square)](LICENSE)

**[Full documentation](https://neewfpv.com/wiki/nexustg)** · [Latest release](https://github.com/newfpv/NexusTG/releases/latest) · [Issues](https://github.com/newfpv/NexusTG/issues)

</div>

NexusTG connects to your Telegram account through Kurigram and uses an Aiogram bot as a private settings panel. It can run context-aware Gemini tools, automate selected private chats, transcribe media, summarize YouTube videos, manage shopping lists, download supported social media, and preserve messages when explicitly configured.

> [!WARNING]
> NexusTG signs in as your Telegram user account. It can read chat history, send messages, download media, and perform enabled account actions. Protect `.env`, `data/`, logs, and backups; use automation and message retention only where permitted and appropriate.

## Highlights

- AI Twin with global and per-chat activation, prompts, search, sleep hours, delays, typing simulation, reactions, and manual override.
- Manual streaming AI, search-backed fact checking, YouTube summaries, and text restructuring.
- Voice, audio, video-note, and video transcription with optional summaries.
- TikTok and Instagram Reel downloads through yt-dlp and optional cookies.
- Native Telegram shopping checklists with command and selected-chat automation.
- Deleted, edited, and self-destructing message preservation to a private forum group.
- Local SQLite state, Gemini key/model fallback chains, cooldown tracking, and per-chat overrides.
- Drop-in Python modules loaded from `modules/` at startup.

## Requirements

- A Telegram account and a BotFather token.
- Telegram `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org/).
- One or more Gemini API keys from [Google AI Studio](https://aistudio.google.com/app/apikey).
- Docker for the recommended Linux/VPS setup, or Python 3.11/3.12 for native use.

## Install

### Windows

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/newfpv/NexusTG/main/install.ps1 | iex"
```

The installer clones to `%USERPROFILE%\NexusTG`, creates a Python 3.11 environment with `uv`, validates the BotFather token, and adds a **Start NexusTG** desktop shortcut.

### Linux / VPS

```bash
bash <(curl -sL "https://raw.githubusercontent.com/newfpv/NexusTG/main/install.sh")
```

The apt-based installer clones to `~/NexusTG`, installs Docker and Compose when needed, validates the token, and starts the service.

### Docker Compose

```bash
git clone https://github.com/newfpv/NexusTG.git
cd NexusTG
cp .env.example .env
# Set TG_BOT_TOKEN and LANG_FILE in .env
docker compose up -d --build
docker compose logs -f nexustg
```

The `./data` directory is mounted into the container and keeps the database, Telegram session, settings, caches, and optional cookies across rebuilds.

## First run

1. Open the BotFather-created settings bot and send `/start`. The first sender becomes the administrator.
2. Enter the Telegram API ID/hash, Gemini key(s), and an IANA timezone.
3. Optionally upload a Netscape-format `cookies.txt` file.
4. Authorize the user account with its phone number, Telegram code, and 2FA password when requested.
5. Test Gemini under **Settings → Test AI**, then enable modules gradually.

## Default commands

| Command | Purpose |
|---|---|
| `.ai question` | Context-aware streaming AI answer |
| `.fact claim` | Search-backed fact check |
| `.text` | Reply to supported audio/video media to transcribe it |
| `.fix text` | Rewrite and structure text; the module is off by default |
| `.fix 5` | Merge and structure up to five earlier messages (maximum 20) |
| `.sum URL` | Summarize a YouTube video |
| `.dl` | Reply to a supported TikTok/Instagram Reel URL to download it |
| `.shop items` | Create or update a Telegram shopping checklist |
| `.save` | Forward a replied message to a configured chat/topic |

Commands and access rules are configurable. See the [complete module and settings reference](https://neewfpv.com/wiki/nexustg#commands).

## Operations

```bash
# Docker logs
docker compose logs -f nexustg

# Restart
docker compose restart nexustg

# Update
git pull --ff-only
docker compose up -d --build
```

Back up `.env` and the complete `data/` directory while NexusTG is stopped. These files contain account credentials and must be stored securely.

## Acknowledgements

Parts of NexusTG's methods and development approach were inspired by [SpyBot](https://github.com/woxov/SpyBot), created by [woxov](https://github.com/woxov). Special thanks for publishing the project and sharing ideas that helped inform NexusTG's development.

## Version and license

The latest tagged release is **v2.2.1**. The `main` branch includes additional post-release Text Structurer and Voice Transcriber fixes. See [releases](https://github.com/newfpv/NexusTG/releases) and the [version guide](https://neewfpv.com/wiki/nexustg#versions).

NexusTG is licensed under the [GNU Affero General Public License v3.0](LICENSE).
