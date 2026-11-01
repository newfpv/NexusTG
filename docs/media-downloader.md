# Media Downloader and AI Twin cache (v2.11.0)

## Link handling

General settings and per-chat overrides now offer **Replace my links** and **Replace others' links** independently. Replacement removes the source only after sending the media. With replacement off, media replies to the original source, preserving its author and link. A manual command uses the source message's author to choose the setting. New installations default to replacing own links and retaining others' links. Existing explicit `replace_link` values remain fallback settings until a new setting is chosen; per-chat values still support Global / On / Off.

The owner's command works either as `.dl URL` or as `.dl` replying to a supported link. An incomplete reply object is fetched once, with a 30-second timeout. Telegram text-link entities are supported as well as visible URLs. A command token must match exactly (case-insensitive); `.dll` is not an alias for `.dl`. The trigger can be changed in settings.

Platform switches govern automatic processing. An explicit supported command still works with automatic processing disabled for that platform. Other people's command permission and group restrictions remain in effect. Unsupported URLs produce a short usage message rather than a silent return. Supported inputs remain TikTok, Instagram Reels (including share/reel links), YouTube Shorts and Pinterest; arbitrary websites and normal long YouTube links have not been added.

Failed extraction is not recorded as a completed job, so a new command can retry it immediately. Active and recently completed duplicates get a short status response. Error messages remain visible; successful temporary status messages are removed. Own uploaded media is excluded from automatic extraction to prevent caption links feeding back into the downloader.

## Bounded extraction

yt-dlp/gallery-dl extraction runs in a subprocess with a four-minute deadline. On timeout or cancellation the process is killed and reaped before its temporary directory is removed. On Linux the entire child process group is stopped, including gallery-dl and ffmpeg descendants. Existing 512 MiB staging and free-space checks remain. Worker results must reference regular files inside their temporary directory.

Downloads still use one bounded queue per account. Managed-account extraction now shares the installation's existing heavy-media slots with Gemini uploads/inference; default concurrency across the group remains four. No extra Telegram authorization or session is created by an extraction worker. Network and platform restrictions can still prevent a download.

## Persistent AI Twin media descriptions

This cache stores text descriptions/transcripts, not retained original media. It is independent of Gemini's temporary uploaded-file cache, YouTube context cache, Telegram dialog-list cache and the deleted-message archive.

The two-day age cleanup is removed. Entries persist across process/container restarts in the account's existing mounted SQLite database. New keys include chat ID, message ID and media type, so identical message IDs in different chats cannot share content accidentally.

On a full 1000-message history read, entries older than that chat's window are removed. The boundary uses Telegram messages before token-budget pruning, including ordinary text messages. Short `.ai` history reads cannot shorten the retained window, and old anchored requests cannot move the boundary backwards or reinsert entries below it. Manual-only usage also retains at most 1000 distinct cached message IDs per chat. Token-budget trimming remains separate: cached material is included only when its message is selected for the current request.

Legacy entries without chat IDs are migrated only when the local archive unambiguously identifies a matching message/type and one peer. Unidentifiable entries remain in the legacy table, unused by scoped lookups. This avoids guessing another chat's identity. Old entries that have already been deleted by the previous two-day cleanup cannot be recovered from that cache.

The deleted-message archive, original Telegram messages, archived text and edit versions are not deleted by this cache window. Their existing retention rules remain separate.
