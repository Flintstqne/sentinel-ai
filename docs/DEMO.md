# Interview demo script (about 5 minutes)

For live screen-share demos (F.N.B. AI & Innovation, Highmark IT Audit). The results on
screen depend on the machine on the day: describe what is actually shown, never what this
script expected.

## Before the call (10 minutes ahead)

- [ ] `systemctl is-active ollama` says `active`. Warm the model so the first explanation is
      quick: `ollama run qwen3:4b "hi" --think=false` (then Ctrl+D).
- [ ] If Wazuh is installed: `sudo systemctl start wazuh-manager`, and cause a few real
      events (see `WAZUH_SETUP.md`, section 6) so SIEM-01 has live evidence.
- [ ] Start Sentinel if it is not running: `.venv/bin/sentinel-ai`. Run a scan from the
      duck's menu so the dashboard shows results from today.
- [ ] Decide whether "AI explanations (local)" starts off (recommended: switch it on live).
- [ ] Close anything private; maximise the dashboard window, not the desktop.

## 1. What it is (30 seconds)

> "Sentinel is a Linux desktop security companion. The duck is the interface; underneath
> it is a read-only control-assessment engine. Seven checks map to NIST 800-53 controls,
> and the core rule is that nothing can report PASS without evidence."

## 2. Evidence-first assessment (90 seconds)

Open the dashboard and pick the firewall row (FW-01).

- Point at **Evidence**: each line says where it came from (a file or a command) and what
  was observed. No opinions in evidence.
- If it is failing on this machine for the usual reason: the service reports `active`, but
  `/etc/ufw/ufw.conf` says `ENABLED=no`. "A naive check would trust the service and pass.
  Sentinel treats the configuration as authoritative and reports the conflict."
- Point out that the live rules needed root, so the check says it did not inspect them,
  rather than pretending.
- Pick any **Not assessed** row: "Missing evidence is UNKNOWN, never a pass."

Audit talking point: this is the difference between a control *configured* and a control
*operating*, and the tool documents which one it could verify.

## 3. Audit trail and export (45 seconds)

- Point at the **Finding** block: ID, first seen, last seen, Acknowledge. Acknowledging
  records that a human saw it; evidence can reopen it.
- Click **Export report**. Open the HTML: evidence, timestamps, and the statement that no
  AI generated or altered any content in the report.

## 4. The AI layer and its constraints (2 minutes; the F.N.B. part)

Switch on "AI explanations (local)" in the duck's menu, select a failing or warning
control, click **Explain with local AI**.

While it answers (5 to 15 seconds), say:

> "The model runs on this laptop through Ollama; the code refuses any non-loopback address
> and bypasses proxies, so evidence never leaves the machine. It receives one assessment,
> and returns a separate object. It cannot change the result, the severity or the evidence,
> and nothing it writes is stored or exported."

When it appears, point at:

- the **label**: "AI-generated (qwen3:4b), may be wrong";
- the **grounding check**: "If the reply names a file, IP, port or user that isn't in the
  evidence, it is discarded and the reason is shown. I tested that by feeding it
  hallucinated ports and paths."
- the **output contract**: JSON with a fixed schema, validated; anything else shows nothing.

Then name the limit yourself (this is the strongest moment):

> "What grounding does *not* catch is wrong reasoning about real facts. In testing, the
> model listed the right ports and then guessed how one was bound. That's why its text is
> labelled, kept below the facts, and never feeds a decision. The governance control here is
> architectural: the AI is a reader, not an author."

## 5. Wazuh (30 seconds)

- If running: select SIEM-01 and show real alerts as evidence, each with its rule ID.
- If not: "Wazuh feeds a seventh check. Right now it's not running, and the check says
  *Not assessed*, because silence from a dead feed is not evidence of safety."

## Likely questions, honest answers

| Question | Answer |
|---|---|
| Why a local model? | Evidence is sensitive host data; it should not go to a third-party API. Also works offline. |
| How do you know the tests mean anything? | 693 tests; key behaviours mutation-checked: I broke each guardrail and confirmed a test failed. |
| Could the model be prompt-injected via log text? | Alert and log text is treated as untrusted. Injection could change the *explanation*, but the explanation cannot change any result, and it is still grounding-checked and labelled. |
| Is this compliance? | No. The NIST mapping is illustrative, and the reports say so. |
| What would you add next? | Score the explainer offline against a fixed set of assessments (grounding failures, refusals) so model changes are measured, not eyeballed. |

Don't claim: production use, enterprise deployment, compliance certification, or that the
grounding check prevents all hallucination.
