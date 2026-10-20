# Independent NexusTG accounts

For Linux with Docker Compose and Python 3.10+. The existing single-account Docker and Windows installations continue to work. The Telegram setup wizard is available in both modes.

## Fresh installation

Clone the repository, enter it and run:

```sh
python3 accounts.py add main
```

Enter the token from your own @BotFather bot; the prompt hides it. Choose RU or EN. Docker builds the image when needed. Open that bot and send `/start`, enter Telegram API credentials, connect your account and follow the guided setup. The Linux installer also accepts `bash install.sh --accounts`.

Each additional account requires its own BotFather controller token:

```sh
python3 accounts.py add second
python3 accounts.py list
python3 accounts.py logs second -f
python3 accounts.py restart second
python3 accounts.py disable second
python3 accounts.py enable second
python3 accounts.py update
python3 accounts.py stop
```

Use `sudo python3` when Docker requires root. `--env-file PATH` imports an existing environment file instead of prompting. `--no-start` prepares a profile without starting it. All profile files stay under `accounts/NAME/`; keep this directory when updating and include it in backups. Do not copy a live Telegram session into a second enabled profile.

Adding the second account restarts the container once to enter shared mode. Further accounts start without restarting existing workers. Returning to one account restarts into direct mode to remove the supervisor's overhead. In shared mode, restarting an individual worker leaves the others running. Updating the image restarts all workers briefly.

## Existing installation

Back up the existing data directory first. The following prepares the image while the old bot runs, stops its named container, binds the original data directory into the new profile and waits for startup:

```sh
python3 accounts.py adopt main --env-file .env --data-dir data --legacy-container NexusTG
```

Use the actual old container name from `docker ps`. The command does not copy or move the data. If startup fails, it disables the new profile and restarts the old container if it was running. Keep the old Compose file for rollback. Never start the old and imported bot together with the same Telegram session. Generated `.accounts.mounts.json` must remain available for the external data mounts; `accounts.py` regenerates it from profile metadata.

## Isolation and resource use

Only imported dependency pages are shared using Linux copy-on-write. Application imports, event loops, bot clients, settings and SQLite connections are created separately after forking. Each worker uses its own `.env`, working directory, data, cookies, temporary files, home directory and caches. This is process isolation for the owner's accounts, not a sandbox for untrusted tenants. Account contents and API requests are never pooled into a common conversation.

Dependencies load before worker startup. The supervisor remains single-threaded; if a dependency starts native threads, it falls back to separate subprocesses. A worker crash restarts that worker with backoff. Heavy Gemini media operations share two file-lock slots across accounts; ordinary text replies keep their independent processing.

An offline three-account test with all modules imported measured about **387 MiB total proportional memory versus 654 MiB** for independent processes. Real usage depends on messages, uploads and caches; use PSS for shared-page attribution rather than adding process RSS. The single-account path uses no additional supervisor process.

The Compose default memory ceiling is 1536 MiB for the whole group, not a reserved allocation. Set `NEXUS_MEMORY_LIMIT` in the installation `.env` to size it for the server. Do not lower it below measured peak load. `NEXUS_GLOBAL_MEDIA_SLOTS` can be set in the Compose environment to adjust the default of two heavy operations. Rotating Docker logs and per-account media caches remain enabled.

For more accounts on a small server, first limit automatic video downloads, unused modules and history budgets. Keep enough disk space for archives and SQLite. CPU and network demand still grow with active conversations; idle account count alone cannot predict capacity.

## Primary bot controls

The first registered profile is the primary controller (`main` is preferred on existing installations). Its assignment is stored in `accounts/manager.json`. Only the primary bot's assigned Telegram owner sees or can use **Add bot** and **My bots**. Additional bots always run with the worker role, even if their environment file tries to override it.

To add a bot, send its BotFather token as text or a small `.txt` file. The token message is deleted and the token is not echoed. Assign your own account or a different numeric Telegram owner ID; forwarding a message with a visible sender also works. Only that owner can initialize and configure the new bot. No keys, prompts, sessions or conversations are copied from the primary profile.

The compact list shows public bot usernames and running/disabled status. Open an added bot's entry to enable, disable or restart it; disabling preserves all data. The primary bot cannot be disabled from this panel. The panel does not expose owner IDs, phone numbers, Telegram sessions, Gemini keys, settings or message contents, and does not read other profiles' conversation databases.

Adding the second bot through Telegram briefly replaces the direct worker with the supervisor, then starts both profiles. Further additions start without restarting existing bots. The Linux installer uses this mode by default for fresh installations. Existing standalone Docker or native installations need the documented adoption step before using managed account controls.

## One installation directory

Keep a single application checkout with all account state underneath it:

```text
NexusTG/
  core/
  modules/
  accounts/
    main/
      .env
      account.json
      data/              # This account's databases, cookies, media and caches
    bot_123456789/
      .env
      account.json
      data/              # A different account's private state
  maintenance/           # Optional private operational backups
```

Existing profiles can keep names such as `pavel` or `masha`; bots added through Telegram use their own generated profile names. Application code is shared, while Telegram identities, settings and all account data remain separate. No full application copy is needed per account.

Both Git and Docker build contexts exclude `accounts/` and `maintenance/`. Store backups in `maintenance/`, and never add that directory to source control or an image. After moving a legacy data directory into its account folder, remove the profile's obsolete `host_data_dir` override and update any external service that mounts its files. Stop the account before moving its database files.
