"""Example named tasks — copy to tasks.py and edit.

claude-launcher imports tasks.py if it exists; without it you just get the
generic "launch a subdir" form.

There are two kinds of button, distinguished by which field you set:

A **Task** (`command`) spawns an interactive `claude` session — driven from the
Claude app via Remote Control — in a fixed workdir with a preset initial prompt,
and is stamped on the iTerm session as user.cl_task so the live list can label it.

A **Dispatch** (`exec`) runs a preset command detached: no `claude`, no Session,
no pane. Nothing appears in the run list and there is nothing to close. Use it for
a fire-and-forget agent that takes one input and leaves its own trace elsewhere.
See docs/adr/0004.

Fields:

    id           stable slug; also the user.cl_task tag value (Tasks only)
    label        button text shown on the page
    workdir      directory to start in (~ expanded; trusted config, so it is NOT
                 confined to CLAUDE_LAUNCHER_PROJECTS_ROOT)
    command      Task: initial prompt sent to claude (usually a /slash-command)
    exec         Dispatch: argv list, exec'd directly with no shell. The seed is
                 appended as one further element, so it can never be word-split
                 or interpolated. Set `command` OR `exec`, never both.
    log          Dispatch only: file (relative to workdir) to append the command's
                 stdout+stderr to. Omit to discard it.
    input        "none"     -> just a button
                 "text"     -> a one-line seed box, appended to command/exec
                 "textarea" -> a multi-line seed box, for a sentence or three
    placeholder  optional placeholder text for the seed box (defaults to the label)
    buttons      optional list of {id, label, args?} — several buttons over ONE
                 shared seed box instead of one button. Each button runs the task's
                 command/exec with its own `args` appended (before the seed), so one
                 Dispatch offers variants that differ only by a flag. With `buttons`
                 set, the task's own top-level `label` is unused; give each button a
                 stable `id` (it is the user.cl_task tag and the launch target).
"""

TASKS = [
    {
        "id": "standup",
        "label": "standup",
        "workdir": "~/work",
        "command": "/standup today",
        "input": "none",
    },
    {
        "id": "capture",
        "label": "capture",
        "workdir": "~/notes",
        "command": "/capture",
        "input": "text",
    },
    {
        # A Dispatch with a button group: no session is spawned; one seed box, two
        # buttons. `jot` runs the script as-is; `log` appends --log first. Both send
        # whatever is in the shared textarea.
        "id": "jot",
        "workdir": "~/projects/my-agents",
        "exec": ["/bin/bash", "scripts/run_jot.sh"],
        "log": "logs/jot.log",
        "input": "textarea",
        "placeholder": "a thought — a thing to do, or a thing that happened",
        "buttons": [
            {"id": "jot", "label": "jot"},
            {"id": "jot-log", "label": "log", "args": ["--log"]},
        ],
    },
]
