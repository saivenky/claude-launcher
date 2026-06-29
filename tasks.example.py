"""Example named tasks — copy to tasks.py and edit.

claude-launcher imports tasks.py if it exists; without it you just get the
generic "launch a subdir" form. Each task spawns an interactive `claude`
session (driven from the Claude app via Remote Control) in a fixed workdir
with a preset initial prompt, and is stamped on the iTerm session as
user.cl_task so the live list can label it.

To add a task, append an entry. Fields:

    id       stable slug; also the user.cl_task tag value
    label    button text shown on the page
    workdir  directory the session starts in (~ expanded; trusted config,
             so it is NOT confined to CLAUDE_LAUNCHER_PROJECTS_ROOT)
    command  initial prompt sent to claude (usually a /slash-command)
    input    "none" -> just a button; "text" -> a text box whose value is
             appended to `command` as a seed
"""

TASKS = [
    {
        "id": "capture",
        "label": "capture",
        "workdir": "~/notes",
        "command": "/capture",
        "input": "text",
    },
    {
        "id": "standup",
        "label": "standup",
        "workdir": "~/work",
        "command": "/standup today",
        "input": "none",
    },
]
