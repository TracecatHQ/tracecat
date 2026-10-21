# Splunk OR-under-AND grouping: the open question, and how to settle it

`references/validation.md` carries a warning that the Splunk backend's unbracketed OR silently
widens a rule. The **observable** part of that is real and reproduces on the installed tooling.
The **stated cause** is wrong, and the **conclusion** is in question. Nothing has been changed
on the strength of that, because removing a warning deserves a higher bar than adding one.

This directory is the minimal test. Someone with a Splunk instance can settle it in ten
minutes.

## What is already confirmed

```
>>> SplunkBackend.precedence   -> (ConditionNOT, ConditionOR, ConditionAND)
>>> SplunkBackend.parenthesize -> False
>>> LuceneBackend.precedence   -> (ConditionNOT, ConditionOR, ConditionAND)
>>> LuceneBackend.parenthesize -> True
```

Both backends declare the same precedence, so precedence is not what differs between them.
Lucene brackets because `parenthesize = True`; Splunk does not because it is `False`. Splunk
Enterprise's own documentation says the search command evaluates OR before AND, which is what
`(NOT, OR, AND)` encodes — and if that is how the emitted query parses, the rule is correct as
emitted and the warning is costing every user real effort for nothing.

Also confirmed: an explicit parenthesised `condition` produces **byte-identical** Splunk output
to the implicit form, so "restructure the condition" is not a fix. Compare
`proc_creation_win_schtasks_or_under_and.yml` with
`proc_creation_win_schtasks_explicit_brackets.yml` — the only difference between the two files
is the condition, and the two emitted queries are the same string.

## Reproduce

```bash
sigma plugin install splunk
sigma plugin install sysmon
sigma convert -t splunk -p sysmon proc_creation_win_schtasks_or_under_and.yml
```

Emitted:

```
EventID=1 (ParentImage="*\\svchost.exe" ParentCommandLine="*-s Schedule*")
OR (ParentImage="*\\taskeng.exe" Image="*\\AppData\\Local\\*") NOT Image="*\\OneDriveSetup.exe"
```

`splunk_spl2` shows the same shape with the operators spelled out, which makes the ambiguity
readable without knowing SPL's implicit-AND rules:

```
FROM main WHERE EventID=1 AND (svchost AND schedule) OR (taskeng AND appdata) AND NOT onedrive
```

## The two readings

| Reading | Parse | Consequence |
|---|---|---|
| **A** — current guidance | `(EventID=1 AND svchost AND schedule) OR (taskeng AND appdata AND NOT onedrive)` | First branch lost the path restriction and the filter. Rule silently **widened** |
| **B** — OR before AND, per Splunk's docs | `EventID=1 AND ((svchost AND schedule) OR (taskeng AND appdata)) AND NOT onedrive` | Exactly what the rule means. Nothing is wrong |

## The test

Index two events and run the emitted query verbatim.

**Event 1** — separates A from B:

| Field | Value |
|---|---|
| `EventID` | `1` |
| `ParentImage` | `C:\Windows\System32\svchost.exe` |
| `ParentCommandLine` | `svchost.exe -k netsvcs -s Schedule` |
| `Image` | `C:\Windows\System32\notepad.exe` (**not** under `\AppData\Local\`) |

Reading A matches this event. Reading B does not.

**Event 2** — tests whether the `NOT` binds to both branches or only the second:

| Field | Value |
|---|---|
| `EventID` | `1` |
| `ParentImage` | `C:\Windows\System32\svchost.exe` |
| `ParentCommandLine` | `svchost.exe -k netsvcs -s Schedule` |
| `Image` | `C:\Users\jdoe\AppData\Local\Microsoft\OneDrive\OneDriveSetup.exe` |

Reading A matches this one too, because the `NOT` sits in the other branch. Reading B does not.

## Reporting the result

- **Neither event returns** → reading B holds. The warning in `validation.md` should be
  rewritten: the emitted query is correct, `check_grouping.py` is enforcing a non-problem, and
  every user paying the hand-bracketing tax on every Windows package should stop paying it.
- **Either event returns** → reading A holds. The warning stands, and only the *stated cause*
  needs correcting, from "precedence mismatch" to "`parenthesize = False`".

Either way, record the Splunk version tested, because this is backend behaviour and it can
change between releases.
