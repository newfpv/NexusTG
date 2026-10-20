# Telegram delivery, presence and archive behavior

Reviewed on 20 October 2026. The API-key module is unchanged.

## Per-account request scheduling

Every running userbot installs its own guard around `Client.invoke`. Account settings, sessions, queues and deadlines remain independent. Downloads use the normal media connections; the guard does not create extra authorized clients.

The guard orders writes within a dialog, serializes account writes, admits at most 128 simultaneous requests and permits four concurrent reads. Local scheduling uses a 0.65-second account gap, a 1.1-second dialog gap and at least 2.5 seconds between edits of the same message. These are conservative application defaults, **not Telegram guarantees or published MTProto quotas**. Duplicate typing actions are merged; typing is skipped during a server cooldown.

Explicit `FloodWait`, `FloodPremiumWait` and `SlowmodeWait` rejections update a private on-disk account deadline. Requests from other modules and requests after restart respect it. Retries retain the same raw request and its message `random_id`. Telegram documents that field as protection against resending. [Message API](https://core.telegram.org/method/messages.sendMessage)

An ambiguous timeout or disconnected send is not automatically resent. Severe authorization/restriction errors pause writes, persist the reason and notify the owner. The owner can acknowledge the pause in Core settings after addressing it; this never clears a live FloodWait. Separate authorization-key locks prevent two managed workers using the same key. Locks cannot detect a copy running on another server. [RPC errors](https://core.telegram.org/api/errors)

Controller bots have an independent Bot API guard: three request slots, ordered operations per chat, a global pacing gap and separate group/chat timing. A large archive upload does not hold another chat's menu. Callback acknowledgement remains responsive. Explicit 429 responses persist `retry_after` and retry after that deadline. Group topics share the group's pacing. [Bot API guidance](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)

## Module changes

| Module | Applied protection |
| --- | --- |
| AI twin | Shared account scheduling and persisted server waits, including force replies, reactions, reads and edits. Existing reply checkpoints remain. |
| AI command | One coalesced preview task; generation continues while preview delivery is blocked. Final output is awaited separately and long answers are split. Commands in a dialog are serialized. |
| Voice transcription | Shared delivery pacing, complete result retained privately before sending, durable queued text blocks. An ambiguous edit is not followed by a second send. |
| Media downloader | One bounded queue per account, event/URL deduplication, serial upload flow, 512 MiB staging budget and a free-space floor. Originals are deleted only after acknowledged delivery. |
| YouTube summary | Shared send/edit scheduling; Telegram delivery failures are distinguished from model errors. |
| Text formatting | Serialized commands; original-message cleanup is journaled only after the replacement succeeds. A restart resumes the unacknowledged deletions. |
| Shopping list | Duplicate-event guard, per-dialog serialization, optional sender allowlist, bot/via-bot/output loop guards. Recent single-message lists are updated instead of posting a fresh list for every automatic event. |
| Custom Saved Messages | Existing receipts remain authoritative. Only confirmed forwards may have their originals deleted; unfinished receipt cleanup resumes after restart. |
| Message saver | Durable notification outbox, event deduplication, topic/setup locks, destination-bound topic records and acknowledged notification parts. |
| Fake activity | Shared action pacing and deduplication, finite positive duration validation, a 24-hour maximum per launch. |
| Profile information | A bounded 30-second in-memory cache; privacy errors are not bypassed. |
| Managed bots | Same-authorization lock across workers; account ownership and manager restrictions remain. |
| Setup wizard | Repeated navigation clicks are debounced; existing stale-render suppression remains. |
| Fact checking | Remains paused. |

Full generated voice/command results and raw pending requests are private account files, never exported through the manager's diagnostic log. Only explicitly marked finished text results and idempotent deletion requests are eligible for automatic restart recovery. Cancelled ordinary replies, group creation, media/forward workflows and ambiguous in-flight requests require review instead of blind replay. A multipart result may require review if the process died between blocks; the complete retained result remains available locally.

## Deleted messages and TTL

The saver receives updates for incoming private non-bot messages selected by its filters. It commits text before waiting for media downloads. It does not scan strangers, join groups to collect messages, or query Telegram to recover already deleted media. Missing or expired media produces a text-only notification. Secret chats are outside this module's implementation.

Three cases differ:

1. Manual deletion: a deletion update identifies a previously cached message. If chat information is missing and the local message ID is ambiguous, the saver skips it rather than assigning it to the wrong person. Text is retained and the notification is queued once.
2. Media TTL/view-once: media `ttl_seconds` includes the view-once sentinel `0x7fffffff`. Telegram specifies content-read and local-disappearance semantics for these media. Saving a copy is not a permitted substitute for those semantics. [View/read rules](https://core.telegram.org/api/views)
3. Chat auto-delete: `message.ttl_period` is an age timer. Telegram requires local deletion when the message expires. The installed Kurigram parser does not expose that field on the high-level Message, so the saver also observes bounded raw update metadata. [Message fields](https://core.telegram.org/constructor/message)

TTL retention remains enabled/disabled by the existing per-account setting, at the owner's explicit request. Turning it off now prevents new TTL text/media caching as well as forwarding. Content marked protected is skipped rather than bypassing forwarding/saving restrictions. [Content protection](https://core.telegram.org/api/content-protection)

The existing media quota remains per account, with forwarded media evicted first and original voice/round videos last. No 180-day text cleanup returns. A hardlink pins the single active upload against cache eviction; its temporary path is removed after delivery/cancellation or during startup cleanup. Private cache/database permissions are restricted. Completed text records remain, including if a dump upload fails.

Bot API notifications have no client `random_id`. Queued notifications survive restarts; an in-flight notification with an unknown outcome is marked for review, and acknowledged parts are recorded. An unconfirmed group/topic creation is not automatically repeated. This reduces duplicate operations, but cannot make cloud and local transactions atomic.

**Limits of the protection:** API Terms prohibit defeating disappearing-content behavior and interfering with accurate status/read indicators. Retaining TTL and always-online behavior keeps a policy risk that pacing cannot remove. Ordinary deletion archives also should not be represented as officially approved: Telegram's privacy policy describes removal from both participants' histories. No official numerical ban probability or guaranteed safe download cadence is published. [API Terms](https://core.telegram.org/api/terms), [privacy policy](https://telegram.org/privacy#10-2-messages)

## Presence timing

On enable, the existing userbot fetches `help.getConfig`, refreshes it at expiry and samples its own `UserStatusOnline.expires` after a status update. Only timing values are written to `data/presence_timing.json`; no IDs, phone numbers or messages are included.

The desired random range is 60–90 seconds. Its upper edge is reduced when necessary to stay below the configured refresh period and sampled expiry with a margin. The one-button toggle and one worker per account remain. Flood waits and network backoff override the refresh interval.

`online_update_period_ms` is a recommended refresh interval. `offline_idle_timeout_ms` describes client idle behavior; it is not proof of server presence expiry. `UserStatusOnline.expires` is the status expiration timestamp, not a guarantee against another client changing the status earlier. A deliberately missed heartbeat was not tested on the owner's account. [Server configuration](https://core.telegram.org/constructor/config), [online status](https://core.telegram.org/constructor/userStatusOnline)

Complaints about unwanted messages remain a separate risk even when timing is conservative. [Spam FAQ](https://telegram.org/faq_spam)

### Deployment observation

On the primary running account, the server returned a 210-second recommended refresh interval and a 30-second client idle timeout. One sampled status had 163 seconds remaining by the server-config clock estimate. The active random range was 60–90 seconds. This is an observation, not a fixed expiration guarantee; the account was not deliberately left without refresh to test a gap. All four independent accounts became ready after deployment. The runtime image was flattened from 121 layers to three while retaining the installed dependencies.
