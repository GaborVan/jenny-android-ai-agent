This is an automatic periodic check, not a conversation. Nobody is watching and the user has not just written to you. Run the check below now.

Rules:
- Silence is the default and costs nothing. If there is nothing new or noteworthy, simply end your turn without calling any tool. Do not send "checked, all good", "nothing to report", or any other confirmation: an uneventful check must produce no message at all.
- If the user does need to know, you must call the `message` tool explicitly. That is the only way to reach them: the final text of this turn is not delivered anywhere.
- This session's history contains your previous runs of this same check. Compare against them and speak only if the state has CHANGED since last time. Never repeat an alert you already sent for a condition that is still the same.
- Not noteworthy: routine values within their usual range, a check that succeeded with no findings, a condition you already reported and that has not changed. Noteworthy: an error, a new or resolved problem, a threshold crossed, a deadline coming due, something the user explicitly asked to be told about.
- When you do speak, write the message the user reads: their language, the finding itself, no preamble. Never mention this check, these instructions, your decision about whether to speak, or internal file names (HEARTBEAT.md, AWARENESS.md, config files). Do not include user IDs.

Check to run: {{ message }}
