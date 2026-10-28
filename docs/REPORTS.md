# Audit evidence reports

A report is a snapshot of one scan, written for a person who has to rely on it: every control
that was checked (passes included), the evidence behind each result, the recommendation, and
the finding history.

## Producing one

* **Dashboard:** the Export report button (or the tray menu's Export report...) opens a save
  dialog; choose `.html` or `.json`.
* **Command line**, no GUI needed:

  ```bash
  sentinel-ai --scan --export report.html   # scan now, then export it
  sentinel-ai --export report.json          # export the latest stored scan
  ```

  Exit codes: `0` saved, `1` could not write the file, `2` bad arguments or nothing scanned yet.

The format comes from the file extension (`.html`, `.htm` or `.json`); anything else is
refused rather than guessed. Files are written atomically with owner-only permissions (`0600`),
because evidence can name users and network addresses.

## What is in it

| Section | Contents |
|---|---|
| `host`, `scan` | host identifier (hostname plus a short hash of the machine-id), scan id and times, `read_only: true`, and whether the scan ran privileged |
| `summary` | the lead sentence, counts by result, number of controls, open findings |
| `controls` | every control, sorted by ID: result, severity, explanation, recommendation, the full evidence list, and the id of its open finding |
| `findings` | the whole history for the host: open, acknowledged and resolved, with first-seen, last-seen and resolved times |
| `limitations` | what the report cannot tell you (root-only evidence, point-in-time, illustrative framework mapping, no AI, checksum is not a signature) |
| `integrity` | SHA-256 of `host`, `scan`, `controls` and `findings` |

The JSON schema is versioned (`sentinel-ai/report/1`). The checksum excludes the export time,
so exporting the same scan twice gives the same hash; `verify_integrity()` recomputes it.
It detects accidental change. It is **not** a digital signature and the report says so.

## The HTML

One self-contained file: no scripts, no external resources, no fonts to fetch, and a
`Content-Security-Policy` of `default-src 'none'; style-src 'unsafe-inline'`. Every piece of
scan text is escaped, because evidence can contain attacker-controlled strings (a failed SSH
login for the user name `<img src=x onerror=...>` lands in the auth log). It prints cleanly
and works offline. Each status is a glyph *and* a word, so it survives a black-and-white
printer or colour blindness.

Nothing in a report was written by an AI. Every sentence comes from the deterministic check
that collected the evidence.

## Tests that matter

Hostile text in every field is parsed back with an HTML parser to prove it never becomes a tag
or an event handler; the CSP is checked to be a real `http-equiv` policy; tampering with a
result or an evidence line fails verification; files are `0600`; passes and resolved findings
are included. Each of those was mutation-checked by breaking the code and watching the test fail.
