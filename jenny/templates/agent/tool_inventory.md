# The tools you actually have

Generated from the running tool registry, so it is true right now:

{{ tool_names|join(', ') }}

This list wins over every other mention in this prompt. Documents, skills and
examples above are written for the general case and can name tools that are not
in your registry — a tool missing from this list does not exist for you, and
calling it wastes a turn.
{% if orchestrator %}
Work that needs a tool you do not have goes to a subagent via `spawn`, which has
its own, wider set. Say what is missing rather than approximating it with the
tools you do have.
{% endif %}
