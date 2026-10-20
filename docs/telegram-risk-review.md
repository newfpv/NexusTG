# Telegram account risk review

Reviewed on 2026-10-20 against the current NexusTG modules. This is a source review and a review of Telegram's published rules, not a prediction of its unpublished anti-abuse thresholds. No functionality was disabled by this review.

An MTProto `FLOOD_WAIT` is a request cooldown, a Bot API `429` limits the controller bot, `AUTH_KEY_DUPLICATED` invalidates an authorization, and an account spam restriction is another condition. They should not all be presented as an account ban.

## Findings and remedies that retain functionality

| Module | Relevant behavior and existing guard | Remaining risk / remedy |
| --- | --- | --- |
| `ai_twin` | Replies to private incoming messages; per-chat tasks, configured delays, exclusions, bot filtering, read/playback receipts and persisted FloodWait checkpoints. | Concurrent chats still share one Telegram account and can collectively produce bursts. Add an account-wide sending queue shared by every module, with per-dialog ordering and a shared cooldown. Use automatic replies in conversations where replies are welcome; generated content or contacts' spam reports remain an independent risk. Delays and a human-sounding prompt do not prevent reports. |
| `ai_command` | Owner commands, optionally other private users; generates a status message and edits a streamed response around every 0.8 seconds. Some edit FloodWait errors are handled. | Frequent edits can exceed limits even with few final messages. Coalesce updates, use a slower adaptive edit cadence and give the final edit priority. Share the account cooldown across modules and bound concurrent command requests per dialog. |
| `voice_transcriber` | Automatic or requested transcription; per-chat queues and appended/edited automatic results reduce repeated messages; bot dialogs and foreign group messages are filtered. | Long results and simultaneous conversations can still send many blocks. Route all blocks and edits through the shared sending queue; merge results where they fit and apply server-requested waits before retrying. Keep transcript generation available while delivery waits. |
| `media_downloader` | Downloads enabled platforms and posts media/albums; foreign group messages and bot dialogs are filtered. | Parallel uploads and repeated automatic links can generate bursts. Add a per-account upload queue, duplicate-URL suppression for the same triggering message, bounded staging storage and server-directed retry. These are scheduling changes, not removal of automatic downloading. |
| `youtube_summary` | Triggered by the account owner's own messages; creates/edits output. | Low exposure when used manually, but it competes with the other modules' sends/edits. Use the same sending queue and distinguish a Telegram delivery error from an AI/API error. |
| `text_struct` | Owner-only command; can edit and replace multiple source messages. | Large batches produce many edits/deletions. Keep batch mode, but queue edits and source deletions, pause on FloodWait and resume only from acknowledged progress. |
| `shop_list` | Supports owner commands, optionally other users, and explicitly selected automatic chats/topics. Checklists fall back to text. | A busy allowed group or repeated third-party commands can make the account post too much. Add per-topic deduplication, sender controls and coalesced checklist updates; keep automatic operation within the owner's selected chats. A bot/other automation in an allowed chat must not create a reply loop. |
| `custom_saved` | New Saved Messages are transferred serially; a receipt is committed before the original is deleted. | Fast streams of saved posts still create forwarding/deletion traffic. Keep the transfer journal and use delayed, idempotent retries with the account cooldown. A delivery failure must not trigger a rapid resend or deletion of an unconfirmed original. |
| `message_saver` | Caches private messages, sends archive copies through the controller Bot API, can create a dump supergroup, add its own bot and create topics. New quotas affect local media only. | Repeated setup clicks can create duplicate groups/invites; parallel topic creation needs a per-user lock. Add setup idempotency, topic locks and a Bot API queue honoring `retry_after`. The legacy forwarding-metadata migration is bounded to 50 original message lookups per batch, five-second spacing and FloodWait backoff. Self-destructing-media retention has a separate rules issue below. |
| `fake_activity` | Explicit owner action; sends a typing/upload/recording action roughly every four seconds per selected dialog. | Many simultaneous or extremely long runs can multiply requests. Share the account action scheduler, deduplicate identical live actions, validate finite durations and honor FloodWait. Fabricated presence/typing can also conflict with expectations about accurate statuses; reducing traffic does not resolve that rules question. |
| `always_online` | One optional task per connected account; sends `account.updateStatus(offline=False)` every 60 seconds, with timeout and FloodWait/network backoff. | One minute is a local default, not an official universal expiry. A future adaptive interval should use `config.online_update_period_ms` and the status expiry instead of guessing that a five-minute interval is safe. Privacy settings and other clients can affect visible status. This task alone sends no messages to contacts. |
| `info` | Read-only profile requests initiated by the controller owner, with a timeout. | Low messaging exposure. Cache repeated requests and back off on Telegram errors; do not bypass privacy restrictions. Profile information should remain in the account owner's controller, not the manager's aggregate logs. |
| `api_keys_info` | Gemini key information/tests in the private controller; raw keys require the owner's explicit reveal action. | Gemini quota or key failures do not themselves ban a Telegram user. Excessive controller menu updates can receive Bot API limits; coalesce progress displays. Keep key values out of manager diagnostics. |
| `managed_bots` | Primary-owner-only management; distinct controller tokens and profile data. Names, quotas and structured diagnostics are shown. Added profiles cannot manage other profiles. | The requested first-`/start` ownership rule means the intended owner must be the first person to start a new bot. Do not clone a logged-in user session into multiple live workers. Controller-token uniqueness does not prove that two profiles use distinct Telegram user authorizations. |
| `setup_wizard` | Owner's controller UI; 22 resumable steps after initial configuration. | No independent user-account messaging loop. Avoid duplicate callbacks and overly frequent controller edits; network waits must not make the menu unusable. |
| `fact_checker` | Paused: registers no userbot message handler and exposes no normal settings entry. | No current automatic Telegram traffic from fact checking. Saved configuration is retained. |

## Cross-module priorities

1. Introduce **one delivery/action scheduler per Telegram user account**, rather than independent delays in each module. Keep queues and limits separate between different accounts. Telegram does not publish a universal safe requests-per-second value; honor the actual RPC cooldown instead of treating an invented number as a guarantee.
2. Persist shared FloodWait deadlines; new incoming messages or commands must not bypass them. Treat `PEER_FLOOD`, `USER_RESTRICTED` and invalidated sessions as conditions requiring owner attention, not errors for a fast infinite retry loop. Controller Bot API retries require their own queue/cooldown.
3. Give each running userbot a separate login authorization. Stop the previous worker before transferring an existing authorization. Concurrent main connections sharing an authorization can invalidate that key; media DC transfers are exempt. This is a session failure, not automatically an account ban.
4. Keep automatic destinations explicit and expected by recipients. Sender consent and complaints cannot be replaced by randomized delays, typos or an impersonation prompt.
5. Use structured diagnostic events, not raw Telegram/AI exceptions or Docker logs, when reporting to the primary bot. The new diagnostic panel already implements this export boundary; raw server logs can still contain private debug output and must stay private.

## Rules that cannot be solved with rate limiting

Telegram API Terms §1.4 forbids retaining self-destructing content and interfering with accurate online/read/typing behavior. Rate limiting cannot make those behaviors compliant. Long-term archives can instead use ordinary messages intentionally shared for retention. This review kept the requested functionality.

Section 1.5 also restricts Telegram-data use for AI. Check the linked licensing terms for the actual data flow; changing models or request pacing does not resolve a prohibited use.

Multiple accounts on one server/IP are not, by themselves, a published fixed ban threshold. The reviewed sources do not support claiming that “three accounts per IP is always safe” or that a proxy makes abusive traffic safe. Separate account settings/data improve isolation, not immunity from Telegram's rules or recipient reports.

## Primary sources

- [Telegram API Terms of Service](https://core.telegram.org/api/terms), especially §1.4, §1.5 and breach procedures.
- [Spam FAQ](https://telegram.org/faq_spam): unwanted messages and recipient reports.
- [RPC error handling](https://core.telegram.org/api/errors): FloodWait and authorization duplication.
- [Server configuration](https://core.telegram.org/constructor/config): `online_update_period_ms` and offline timing fields.
- [Online status expiry](https://core.telegram.org/constructor/userStatusOnline).
- [Updating online status](https://core.telegram.org/method/account.updateStatus).

The queues, pacing and additional guards listed as remedies above are recommendations from this review unless explicitly described as existing behavior. They have not all been implemented by the account-management/archive update.
