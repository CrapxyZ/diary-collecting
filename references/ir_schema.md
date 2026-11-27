# Worklog Intermediate Representation, v1

## Files and frontmatter

A managed log is UTF-8 with LF line endings, named YYYY-MM-DD_slug.md. The script adds _2, _3, etc. Slugs use 1–64 lowercase ASCII letters, digits, or hyphens, starting with a letter or digit. The date is the calendar date at start in the runtime's local timezone. Across midnight the same file remains active under its original date.

The frontmatter opens and closes with a line containing three hyphens. In between, use one known key and one JSON value per line, separated by a colon and a space. Emit no comments, bare scalars, block values, aliases, or duplicate keys. JSON values are a restricted YAML-compatible representation. Use an empty list when there are no exclusions. The script owns the complete envelope.

| Field | Meaning |
| --- | --- |
| schema_version | Integer 1 |
| date | Quoted YYYY-MM-DD |
| session_id | Canonical lowercase UUID |
| session_key | Lowercase selector, default default |
| worktree_id | SHA-256 of the resolved worktree root or non-Git cwd |
| workspace | Display name of that root |
| project_id | Stable key, default SHA-256 of the resolved Git common directory, or of cwd outside Git |
| project | Project display name, optionally corrected by Agent |
| topic | Short session subject |
| status | active or frozen |
| created_at, updated_at | ISO 8601 with numeric UTC offset |
| start_anchor, last_sync_anchor | Object with kind event or marker, and an opaque string value |
| excluded_sessions | Array of objects with file and topic for earlier same-date frozen sessions |

Keys and explicit project IDs match [a-z0-9][a-z0-9_-]{0,63}. Header text is printable, trimmed, and at most 300 characters. Instance IDs do not contain raw machine paths or remotes. Moving the checkout or cloning again can change default derived IDs. Set the same project_id explicitly at init when local clones intentionally belong to one project.

## Body and updates

Keep the headings and their order. After PRIOR_CTX allow zero to three nonblank, printable lines of at most 500 characters without Markdown headings. Put a blank line between PRIOR_CTX and LOG. The remaining four sections may be empty. Separate columns with space, vertical bar, space. Within fields write \| for a literal bar and \\ for a literal backslash. Use a single hyphen for an unknown or inapplicable field. Row numbers start at 1 per section and increment without gaps. Keep the numerical order and existing IDs in every update. The script compares the complete file version, then checks the replacement LOG and preserves the original PRIOR_CTX bytes.

    ## PRIOR_CTX
    Full test suite was slow before this session.

    ## LOG

    ### WORK
    T1 [DOING] isolate fixture setup | tests/test_api.py | timings captured | -

    ### METRICS
    M1 suite duration | - -> 42 s | local cold run | -

    ### PITFALLS
    P1 [OPEN] duplicate fixture initialization | suspected module import | - | -

    ### DECISIONS
    D1 profile cold runs first | removes warmed-cache variance | - | -

Columns and states are fixed.

| Section | Fields after ID and optional state |
| --- | --- |
| WORK | [TODO, DOING, DONE, BLOCKED, SUPERSEDED] action, artifacts, delta, revision note |
| METRICS | target, before -> after, measurement context, revision note |
| PITFALLS | [OPEN, RESOLVED, SUPERSEDED] symptom, root cause, fix or workaround, revision note |
| DECISIONS | choice, rationale, superseded-by ID or reference, revision note |

After a follow-up verifies the root cause and reports a comparable run, replace the old rows in place.

    T1 [DONE] isolate fixture setup | tests/test_api.py | fixture reused across requests | DOING -> DONE
    M1 suite duration | 42 s -> 29 s | same local cold-run command | baseline recorded
    P1 [RESOLVED] duplicate fixture initialization | fixture was request-scoped | reuse session fixture | import hypothesis rejected

Appending a second row for the same resolved issue leaves two contradictory statuses. Keep P1 and revise it. If a decision changes, set D1's superseded-by to D2 and add D2 with the replacement and rationale. Keep elapsed comparisons and actual units in METRICS. Qualitative feedback belongs in WORK or PITFALLS when it has no numeric measurement.

### Close and restart in the same conversation

Suppose 2026-11-27_fixture.md is frozen after resolving P1. A second start may note in PRIOR_CTX that the suite now reuses fixtures. Its new LOG is empty, and excluded_sessions includes the prior filename and topic. On the next update, recording “fixed duplicate fixture initialization” again would import finished history. If the new session demonstrates a teardown failure caused by that earlier change, its own P1 may describe that new failure and reference 2026-11-27_fixture.md#P1. If the second session's waterline disappears, pause and get the missing range or a user-supplied delta before editing.

## Script contract

python3 <skill-root>/scripts/worklog_ctl.py <command> [arguments]

All commands output one UTF-8 JSON object on success. Errors go to stderr as a JSON object with error and message, with structured issues or conflict information when available. Exit codes are 2 for usage, 3 for missing active sessions, 4 for conflicts or frozen targets, 5 for schema/data errors, and 6 for I/O or partial failure. Use a serializer to pass the payload through stdin as data.

| Command | Input and effect |
| --- | --- |
| init [slug] | JSON with topic, prior_ctx (array of lines), start_anchor, and optionally project and project_id. With --auto-end also provide the previous session_id and expected_revision. The same start anchor in the same scope is safe to check after an uncertain response. |
| status | Scope metadata and active candidate, or null. Repairs the cache when possible. |
| get-active | Scope metadata, complete file as log, session_id, last_sync_anchor, and SHA-256 revision. |
| update | JSON with session_id, expected_revision, old_anchor, new_anchor, and log starting at ## LOG. Optional project changes its display name. A change confined to project or section spacing does not advance the anchor. |
| freeze | JSON with session_id and expected_revision. Optional final_update contains old_anchor, new_anchor, log and optionally project, applied in the same replacement. |
| list-by-date [date] | Date and metadata-only candidates. Shows malformed headers separately as issues. |
| collect [date] | Selected valid source snapshots with revision and full log, plus any unresolved header issues from key discovery. |
| save-brief [date] | Markdown on stdin. Use --overwrite only for an explicitly authorized replacement. |

For the first five commands, --cwd and --key identify the selected worktree and session key. Copy revision into expected_revision exactly and last_sync_anchor into old_anchor. A conflict or frozen result means the proposed write was rejected. An index warning on a successful result means the Markdown commit happened. A failed auto-end may include frozen metadata even though the new file was not published. Re-read status, then start without auto-end if the scope is empty and the original start was still requested. All dates default to the runtime's local calendar day.
