# MotionDesk

A governance motion starts as prose. Prose is where the usual failures live: a motion that asks for two things at once, a target that is not an address, a value that is not a number, a summary that promises more than the action does. MotionDesk does not execute the prose and does not trust it. It reads the prose once, by consensus, parses the reading into a closed set of fields, and lets the voters decide on the parsed action rather than on the paragraph.

## How a motion runs

```
submit_motion   the author writes the body in prose, fixes the vote window and the timelock
structure       one consensus round reads the prose into PAY | CALL | TEXT
delegate        an address hands its vote to another; an empty string takes it back
vote            yes | no | abstain, recorded under the resolved identity; a revote overwrites
tally           after the window closes: at least 3 identities and yes > no
execute         after the timelock expires, once, by anyone
```

A motion is `draft` until it is structured, `voting` while the window is open, then `passed` or `failed` at the tally, and `executed` after the timelock. A reading the desk refuses leaves the motion in `needs_revision` with a reason the author can read and fix.

## The interesting part

**The model is asked for an action, not for permission.** One consensus round reads the body and returns `action_type`, `target`, `value`, `params`, `summary`. The parser accepts only `PAY`, `CALL` or `TEXT`; a target that is empty or `0x` plus exactly 40 hex characters; a value that is a whole number of zero or more; params that are an object of strings serialised under 2,000 characters; and a summary under 280 characters. Nothing else survives. The model cannot set the window, the quorum, the delegations or the timelock, and it cannot make a motion pass.

**A refused reading is a result, not a crash.** When the validators return broken JSON, an unknown action type, a bad target or an ambiguous reading, the contract stores `needs_revision` and a reason, and returns. The transaction does not revert: the author has to see why the motion was refused, and `revise` sends it back to `draft` for another round.

**Ambiguity fails closed.** The comparative principle requires both answers to agree on the action type, the target, the value and the params; the wording of the summary is free. If either answer flags `ambiguous`, the resolved answer is ambiguous, and an ambiguous reading is refused rather than guessed at.

**Votes are counted by resolved identity.** `delegate(to)` records that an address votes as another. `vote` resolves the sender through the delegation chain — at most five hops, and a cycle or a longer chain falls back to the address that actually sent the transaction — and records the choice under that identity. Two addresses that resolve to the same identity are one vote, and a revote overwrites the choice and recounts the tallies.

**The tally is integer arithmetic.** Turnout is the number of choices stored, quorum is 3, and a motion passes only when turnout reaches the quorum and yes is strictly greater than no. Abstentions count toward turnout and toward nothing else.

**Passing is not executing.** `tally` writes `passed`; `execute` refuses until the timelock has expired, writes `executed` with a timestamp, and refuses a second call. The window and the timelock are fixed at submission, in seconds, each between 1 second and 30 days.

**Nothing here moves funds.** The contract settles what the motion says and when it may be acted on. A treasury keeps the keys and does the paying; `PAY` is a decision, not a transfer.

## API

| Function | Kind | Who may call | What it does |
| --- | --- | --- | --- |
| `submit_motion(body, vote_window, timelock)` | write | anyone | Writes a `draft` motion. Body 1–4,000 characters; windows 1 second to 30 days. Ids count from 1. |
| `revise(motion_id, body)` | write | the author | Replaces the body of a `draft` or `needs_revision` motion and clears the reason. |
| `structure(motion_id)` | write | anyone | One consensus round over the body. Fills the action and opens the vote, or stores `needs_revision` and a reason. |
| `delegate(to)` | write | anyone | Points the sender's vote at `to`, or at nobody when `to` is empty. |
| `vote(motion_id, choice)` | write | anyone, while the window is open | Records `yes`, `no` or `abstain` under the resolved identity and recounts. |
| `tally(motion_id)` | write | anyone, after the window | Refuses before the window closes; writes `passed` or `failed`. |
| `execute(motion_id)` | write | anyone | Refuses unless the motion is `passed` and the timelock has expired; writes `executed` once. |
| `get_motion(motion_id)` | view | anyone | The whole motion: body, parsed action, status, reason, windows, counters. |
| `get_vote(motion_id, who)` | view | anyone | The raw choice stored under that address, empty if it never voted. |
| `identity_of(who)` | view | anyone | The address `who` votes as, after following the delegation chain. |
| `resolve(who)` | view | anyone | The same chain walk, exposed on its own. |
| `list_motions(offset, limit)` | view | anyone | Newest first, pageable. |
| `stats()` | view | anyone | `{motions, voting, passed, failed, executed, votes}`. |

## Layout

```
contracts/motion_desk.py          the contract
tests/direct/test_motion_desk.py  32 direct-mode tests in 7 classes
scripts/deploy_studionet.js       deploy with genlayer-js, no CLI
scripts/e2e_studionet.js          one motion end to end: structure, vote, tally, execute
```

## Live

- Contract (Studio Network): `0xC7d1e75F3D3391B3f2542bd2E3FE95a660B9c3BF`
- Explorer: https://explorer-studio.genlayer.com/address/0xC7d1e75F3D3391B3f2542bd2E3FE95a660B9c3BF

## One motion on the live contract

The body is prose, and it is the only thing the model sees:

> Pay 250 credits to the gardener at 0x1234567890AbCdEf1234567890aBcDeF12345678 for the spring work on the common garden: soil, seeds and the two benches.

The vote window is 300 seconds and the timelock 120 seconds, so the whole run is real time: the script sleeps through both waits.

| what | transaction |
| --- | --- |
| deploy `0xC7d1e75F3D3391B3f2542bd2E3FE95a660B9c3BF` | `0x1c51a9b96456169057173daf080edc8090106139a7a4da786fa06504b1089080` |
| `submit_motion` — prose, window 300s, timelock 120s | `0x8ca175e7864aff7cdba6af0a6e014986dac31f8197d178f4a56463c40193529c` |
| `structure` — one consensus round, PAY 250 to the gardener | `0x54d84cc6d50e2c7de2267a038a512dbd8603d6d153963d11bf53337477044907` |
| `vote` yes — voter a | `0xdb55860d8bffc76cacd272de3058d366a7ae31e12f0946f055801895a1bb0dd0` |
| `vote` yes — voter b | `0x8e357871ed2df2ee211b963a56cbf5b38886145605bdcc0dc3920efcfef2224d` |
| `vote` yes — voter c | `0x60a77d88a55d2d46024b72b323b525bdd9f0566e309c194d3bb04042810ef58a` |
| `tally` before the window — refused | `0x00a9c4ccd8d35f13ffbc7a9fc8f390f8aebb90486a9e693d031af5ebc4946ed1` |
| `tally` after the window — passed, 3 of 3 yes | `0xc63361aa501ed2cb05019e7d0c91b753413b6a6557ef4d4afdbabd2231acb470` |
| `execute` before the timelock — refused | `0xbad5062973e87c756f86939723163efea1d6fb5e3c175fc45ef4e95d017a7094` |
| `vote` after the window — refused | `0xf10668d0b3e9252fc5c4968d4431237ec3b703604c8dfe289f25d0e909e2d67b` |
| `execute` after the timelock — executed | `0x6cafae9c6dedd58b70c47e3c0e9820d77d97a7e135c7e5fdab6a7cae9e2a9307` |
| `execute` a second time — refused | `0x85381da41e2459578d182445c81cc817239fc15763a98e892f6b02ef48a2ea03` |

The parsed action, as written by the contract:

```
action_type  PAY
target       0x1234567890AbCdEf1234567890aBcDeF12345678
value        250
summary      Pay 250 credits to the gardener for spring work on the common garden, including soil, seeds and two benches.
```

## Tests

```
env -u PYTHONPATH -u VIRTUAL_ENV -u PYTHONHOME uv run --python 3.13 \
  --with pytest --with genlayer-test pytest tests/direct -q
```

32 direct-mode tests. The bodies and the model are mocked; the contract is not, and time is moved with `direct_vm.warp()`. They cover the draft and its bounds, the reading into a PAY and a CALL action, an unknown action type, broken JSON, an ambiguous reading that fails closed, a bad target, a negative value and an over-long summary, revising a refused motion and the author-only rule, structuring twice, the three choices and their counters, a revote that flips them, a vote after the window, a vote outside the voting phase, delegation counting one vote under the resolved identity, a cycle and an over-long chain, tallying below the quorum, a yes majority, yes level with no, tallying early and twice, executing before the timelock, executing once after it, a failed motion that cannot be executed, and the views.
