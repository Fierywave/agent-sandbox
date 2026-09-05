# Handoff Note

Copy this file to `HANDOFF_<track>_<phase>.md` (e.g. `HANDOFF_A_phase1.md`)
and commit it before swapping tracks, or at the end of any phase you want a
clean record of.

```
Track: A / B
Phase completed: __
Done:
  -
Half-done / in progress:
  -
Deviations from the Phase 0 contract (if any):
  -
Known issues / things that will bite the next owner:
  -
Where to start next:
  -
```

## Session Kickoff Prompt (paste this to your coding assistant first)

> Before we start, ask me: "Are you working Track A ('The Guardrail') or
> Track B ('The Agent') today?" Wait for my answer. Then ask me to paste the
> latest `HANDOFF_*.md` from the *other* track, if one exists yet. Treat that
> handoff note as ground truth for what's already been built on the other
> side — don't assume anything about it that isn't written there, and flag
> anything in my current code that looks like it contradicts it. Once you've
> confirmed which track I'm on and read the handoff (or confirmed there
> isn't one yet), summarize back to me in 2-3 sentences what you understand
> the current state to be, then we'll proceed with that phase's tasks.
