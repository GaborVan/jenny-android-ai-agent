[Subagent '{{ label }}' {{ status_text }}]

Task: {{ task }}

Result:
{{ result }}

Summarize this naturally for the user. Keep it brief (1-2 sentences). The user can see the running subagents and their ids in the UI, so do not pretend this work happened in the chat: if it is useful, name the subagent or its id, and never deny having delegated the task. Just do not narrate the mechanics they can already see.
