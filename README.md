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
| `.fix 5` | Process exactly five earlier messages, including attachments (maximum 20) |
| `.fix 2 transcribe both conversations` | Read two earlier messages and apply the task after the count |
| `.sum URL` | Summarize a YouTube video |
| `.dl` | Reply to a supported TikTok/Instagram Reel URL to download it |
| `.shop items` | Create or update a Telegram shopping checklist |
| `.save` | Forward a replied message to a configured chat/topic |

Commands and access rules are configurable. See the [complete module and settings reference](https://neewfpv.com/wiki/nexustg#commands).

The `.fix` count includes text and attachments from either sender, in chronological order before the command in the same chat/topic. MP3 and other audio files also work when sent as documents. Photos, video, PDF and common text/Office documents are read before the final task runs. Unsupported files, missing messages and failed attachments stop the batch with an explicit error and preserve all sources; a caption alone never substitutes for an unread file. Large text extractions are rejected rather than silently truncated (256 KiB per text/Office file, 512 KiB of extracted batch text). A successful batch retains the existing behavior of replacing its source messages after the complete result is delivered.

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

The latest tagged release is **v2.9.3**. See [releases](https://github.com/newfpv/NexusTG/releases) and the [version guide](https://neewfpv.com/wiki/nexustg#versions).

NexusTG is licensed under the [GNU Affero General Public License v3.0](LICENSE).

## Primary bot and independent added bots

Fresh Linux installations create a primary controller. Only its owner can open **Core settings → My bots** and add another BotFather token there. The first person to send `/start` to a new bot becomes its owner; existing owners are preserved. Added bots never receive bot-management controls. The list uses the connected account’s name, with an editable management label. Each entry provides archive quotas and a bounded diagnostic log that excludes messages, phone numbers, keys, sessions and raw exception text.

For a manual Linux installation, run `python3 accounts.py add main` and then `/start` in that bot. Existing standalone installations can migrate with `accounts.py adopt`; see [account management](docs/accounts.md). Native Windows single-account startup remains available.

## Guided setup and independent accounts

After Telegram login, a resumable 22-step wizard explains every settings section, including prompts, history, cookies and modules. Keys, models and time zone remain in core settings and are not repeated in the walkthrough. Reopen it from Settings > Guided setup.

Linux servers can run independent accounts from one installation with preloaded dependencies and shared memory pages. Each account has a separate process, environment, database, Telegram session, archive and media directory. A single account runs directly without an extra supervisor process.

See [installation, adding accounts and migration](docs/accounts.md).

## Archive storage and chat pages

Each account has a separate local media quota (2 GiB by default), editable by the primary owner in **Core settings → My bots → account → Archive media limit**. Forwarded media is evicted first, old heavy attachments next, other attachments after that, and original voice messages / round videos last. Within a class the oldest files go first. Text and edit versions have no age expiry and are never removed by the media quota. Text databases can therefore continue growing independently. Setting the media limit to zero removes existing local media and stops new media caching; Telegram dump-chat copies remain untouched. A lower limit is applied by the account worker within 30 seconds after an active download finishes. Usage shows local archive media, text database size and the server filesystem’s used/free space. Other applications are included in the filesystem figures.

**My chats** shows ten private chats per page, one button per row. Next-page arrows load more dialogs, including archived private chats, without truncating the first 30 mixed dialogs. Groups, bots and Telegram service messages remain excluded from this AI conversation picker.

[Telegram module risk review and remedies](docs/telegram-risk-review.md) covers every module, shared request pacing, session ownership, online timing and API-terms limitations.
