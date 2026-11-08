---
name: week
description: Weekly training report for one ISO week (analyst, then planner, then a markdown report). Run it as /week 2026-W42.
argument-hint: "[ISO week, e.g. 2026-W42]"
disable-model-invocation: true
allowed-tools: Bash(date *)
---

# /week: weekly training report

Today is !`date +%F` (current ISO week: !`date +%G-W%V`).
Last complete ISO week: !`date -v-7d +%G-W%V 2>/dev/null || date -d '7 days ago' +%G-W%V`.

Requested week: `$ARGUMENTS`

## 1. Pick the week

- If the requested week above is empty, use the last complete ISO week.
- It must look like `YYYY-Www` (e.g. `2026-W42`). If it does not, stop and tell the user the
  expected format.
- If it is the current ISO week, the week is still in progress: mention it in Data caveats.

## 2. Analyst (what happened)

Call the Agent tool with `subagent_type: "analyst"` and the prompt `<the week>`.

It must return one JSON object with exactly these keys: `week`, `distance_km`, `runs`,
`longest_run_km`, `change_vs_prior_avg_pct`, `zero_weeks_streak`, `data_complete`,
`blocked_count`, `flags`, `inferred_flags`, `suggested_max_next_week_km`,
`fallback_max_next_week_km`,
`next_week_start`, `notes`. If the reply is not valid JSON or keys are missing, call
the analyst once more saying what was wrong. If it fails again, stop and show the raw reply.

## 3. Planner (next week)

Subagents share no memory, so the hand-off is explicit. Call the Agent tool with
`subagent_type: "planner"` and this prompt:

```
Analyst JSON for <week>:
<the analyst JSON, verbatim>
Plan the week starting on next_week_start.
```

## 4. Report

Do not call any tool yourself, and do not recompute any number: use the JSON and the
planner's output as they are. You cannot see the subagents' tool calls: never claim a
subagent did or did not call a tool. If the planner states a fact that is not in the analyst
JSON, keep it as it is (the planner can read more history than the analyst). Write the report in this exact format:

```
# Training week <week>

## Summary
<2 sentences: what the week was and what next week is for.>

## What happened
| Metric | Value |
|---|---|
| Distance | <distance_km> km |
| Runs | <runs> |
| Longest run | <longest_run_km> km |
| Change vs prior weeks' average | <change_vs_prior_avg_pct> % (or "n/a" + why) |
| Consecutive 0 km weeks | <zero_weeks_streak> |
| Next week's ceiling (computed) | <suggested_max_next_week_km> km (or, if null, "<fallback_max_next_week_km> km: safety cap, incomplete data") |

<analyst notes>

## Flags
<one bullet per entry of `flags` with a plain-English meaning (computed by the server), then
one bullet per entry of `inferred_flags` marked "(inferred from the run name)". "None." if
both are empty.>

## Next week plan
<the planner's output: question for the runner (if any), 7-day table, reasoning>

## Data caveats
<bullets: missing heart rate, blocked activities / incomplete data, week still in
progress, null % change. "None." if nothing applies.>

---
*Not medical or coaching advice.*
```

Output only the report.
