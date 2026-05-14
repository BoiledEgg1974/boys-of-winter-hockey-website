# Sound and haptics (mobile shell)

## Principles

- **Short** UI sounds (about **0.1–0.35 s**); avoid long loops during drafts.  
- Respect **iOS silent switch** and Android **do not disturb**; never use sound as the only signal (pair with **visual + optional haptics**).  
- Default **master mute** or conservative defaults; many GMs run drafts in meetings.  
- **Per-category toggles** in native settings: Draft, Trades, Messages, Scores (and a master mute).  
- **VoiceOver / TalkBack:** never convey exclusive information through sound alone.

## Suggested mapping (character)

| Moment | Suggested character | Notes |
|--------|---------------------|--------|
| Draft: your pick / clock warning | Soft tick or glass ping; urgent variant near deadline | Add light haptic; avoid polarizing arena horns by default. |
| Draft: pick confirmed | Short affirmative chirp or puck tap | Distinct from error. |
| Draft: queue / board update | Neutral whoosh or two-tone blip | Signals change without alarm. |
| Trade: incoming proposal | Two-note chime (different family from draft) | Tied to push category “Trades”. |
| Trade: accepted / declined | Success chime vs soft low thud | Keep decline neutral, not shaming. |
| GM message | Very subtle pop | Often muted by default. |
| Game start / final (your team) | Optional 0.2 s sting | Tie to “Scores”; consider quiet hours in settings. |
| Pull-to-refresh / sync | Haptic-only or tiny snap | Sound often unnecessary. |
| Error / blocked action | Soft clunk | Accessibility-friendly, not a harsh alarm. |

## Implementation sketch

- Ship assets as **small compressed** clips (consistent loudness).  
- **Preload** at app launch for draft room latency; test **Bluetooth** latency.  
- Shell reads **same categories** as push preferences so in-app sounds and notifications stay aligned.
