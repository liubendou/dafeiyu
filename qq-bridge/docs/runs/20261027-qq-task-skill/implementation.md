QQ Task Dispatch Skill — implementation status

Status: implemented; pending owner end-to-end test.

Behavior added to the QQ persona: when owner QQ <OWNER_QQ> explicitly assigns a multi-step task, select the model from the conversation/model note (default deepseek-v4.1-flash, never auto-upgrade to Pro), call arena_dispatch(task, model, note), report only that it was accepted/dispatched, then use arena_tasks to track and relay the actual result.

Workbench guidance: each profile is a separate persistent browser identity/session (cookies/login/proxy), not a model. A profile number is never a model selector. workbench_status is for status; workbench_action is only used for status/open/close when the owner asks.

Implementation is through the existing direct Arena task-dispatch MCP path; it does not physically copy/paste into a visible browser. No processes were restarted and no profiles opened.

Validation: the main web cordis patch parses with the installed js-yaml parser. The two preset files report duplicate mapping keys in both their pre-change backups and post-change copies (pre-existing parser issue); the inserted skill is a prompt-text line. End-to-end QQ acceptance remains pending.
