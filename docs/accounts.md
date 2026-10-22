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

Dependencies load before worker startup. The supervisor remains single-threaded; if a dependency starts native threads, it falls back to separate subprocesses. A worker crash restarts that worker with backoff. Heavy Gemini media operations share four file-lock slots across accounts; ordinary text replies keep their independent processing.

An offline three-account test with all modules imported measured about **387 MiB total proportional memory versus 654 MiB** for independent processes. Real usage depends on messages, uploads and caches; use PSS for shared-page attribution rather than adding process RSS. The single-account path uses no additional supervisor process.

The Compose default memory ceiling is 1536 MiB for the whole group, not a reserved allocation. Set `NEXUS_MEMORY_LIMIT` in the installation `.env` to size it for the server. Do not lower it below measured peak load. `NEXUS_GLOBAL_MEDIA_SLOTS` can be set in the Compose environment to adjust the default of four heavy operations. Rotating Docker logs and per-account media caches remain enabled.

For more accounts on a small server, first limit automatic video downloads, unused modules and history budgets. Keep enough disk space for archives and SQLite. CPU and network demand still grow with active conversations; idle account count alone cannot predict capacity.

## Primary bot controls

The first registered profile is the primary controller (`main` is preferred on existing installations). Its assignment is stored in `accounts/manager.json`. Only the primary bot's Telegram owner can use **Core settings → My bots**. **Add bot** is available only inside that panel. Additional bots always run with the worker role, even if their environment file tries to override it.

To add a bot, send its BotFather token as text or a small `.txt` file. The token message is deleted and the token is not echoed. The first person to send `/start` in the new bot’s private chat becomes its owner. Concurrent starts are claimed atomically, and later users cannot take over. Ownership is persisted in the profile independently of resettable settings; existing database owners are pinned on their first interaction. Existing profile owners are retained. No keys, prompts, sessions or conversations are copied from the primary profile.

The compact list shows the connected Telegram account’s name and running/disabled status. Rename an entry to set a management-only label. Open an added bot's entry to enable, disable or restart it; disabling preserves all data. Each profile, including the primary, also has media-quota and safe recent-log controls. The primary bot cannot be disabled from this panel. The panel does not expose owner IDs, phone numbers, Telegram sessions, Gemini keys, settings or message contents, and does not read other profiles' conversation databases.

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

### Media quotas and diagnostics

The default local archive media quota is 2 GiB for each profile, stored as `archive_limit_bytes` in its `account.json` when customized. The primary owner can change this limit in the account card; profile owners cannot change other profiles’ limits. Workers enforce it on startup, after media writes and every 30 seconds. Downloads are serialized per account and checked against both the individual file limit and total quota. One active file is staged in a separate temporary directory, bounded by the per-file limit, before atomic admission and priority eviction; temporary disk usage is additional to the retained-media quota. Forwarded media is removed first, heavy non-forwarded media next, small attachments after that, and original voice / round videos last. The oldest files within each class are removed first. Legacy forwarding metadata is recovered from surviving original messages in batches of up to 50 every five seconds, with FloodWait backoff; unavailable originals remain unclassified. Text rows and old edit versions are retained without an age limit. A 64 MiB free-disk reserve can trigger earlier media eviction to protect text/database writes; databases themselves are never pruned. Abandoned saver staging folders are cleaned on worker startup. Successful forwarding to the Telegram dump does not immediately remove a local copy.

`data/archive_usage.json` contains only aggregate byte/file counts. Diagnostics are rotated at 128 KiB with two backups. The panel exports at most 100 structured entries containing UTC time, severity, source location and whitelisted error categories. Free-form log messages, tracebacks, chat content and credentials are never exported. These controls are for owner-managed profiles and do not turn the shared filesystem into a sandbox for hostile plugins.


## Remove an additional bot (v2.10.0)

Only the primary controller owner can remove an additional profile: **Core settings → My bots → account → Remove bot**. A separate confirmation names the account and warns that its local settings, Telegram session, caches and archive will be permanently deleted. The primary profile is protected. The supervisor first stops and reaps the selected worker, then removes that profile; other workers keep running. Telegram messages and the BotFather bot itself are not deleted or revoked. A removed token can be registered again as a fresh profile.

Legacy accounts using external `host_data_dir` mounts require server-side removal; the menu refuses to traverse external storage. Symlink targets outside a profile are preserved. Interrupted local cleanup is retried on startup. Removal is deliberately not exercised on existing production accounts during automated tests.
