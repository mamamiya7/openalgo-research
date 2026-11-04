# ChatGPT connection and six small decision cases

## Result

The connection works; reliable autonomous research is **not qualified**.
The user completed OAuth in OpenAlgo. `chatgpt/gpt-6.1-sol` is enabled and
default. Its credential test passed in 5.359 seconds. A real Agent UI turn
called `research_capabilities` successfully. Trading remained disabled.

| Controlled case | Actual observed behavior | Qualification |
|---|---|---|
| A: whole-share affordability | Read the 77 native affordability blockers, then promised to open a plan without calling it. One explicit continuation repeated the promise. No experiment. | Fails to act; capital selection not exercised. |
| B: size clipping | After one explicit continuation, opened the confirmed plan, proposed `funding_and_size`, advanced, then read the native stopping evidence. Three native remedy accounts remained blocked. | Safe rejection; automation and feasible sizing are not solved. |
| C: fixed position cap | After one explicit continuation, opened a plan which immediately stopped with no permitted remedy. No experiment or limit change. | Correct retention, but needed a nudge. |
| D: missing prices | Stopped after inspecting evidence; did not invent missing prices or increase funds. | Correct unassisted stop. |
| E: cost fragility | After one explicit continuation, selected the advertised `lower_turnover` remedy. Proposal rejected because the frozen holding grid has no alternative. | Planner advertises an unexecutable action. |
| F: temporal instability | After one explicit continuation, selected the advertised `temporal_stability` remedy. Same exhausted-grid rejection. | Planner advertises an unexecutable action. |

No case produced an accepted result. Consequently this live-model batch did
**not** exercise accepted-result saving/reopening; separate deterministic tests
cannot substitute for that missing live evidence. No false accepted-result or
independent-validation claim was observed in the final case responses.

## Scope and evidence

These are six frozen one-symbol fixtures with deterministic synthetic prices,
not six completed scanner studies or six full optimizations. A–D use diagnoses
from actual native baseline receipts. E–F have actual native baselines but
explicitly controlled cost/prefix-failure diagnostic inputs. They test reaction
to a supplied diagnosis, not discovery of that diagnosis. Exactly six baseline
accounts and three additional native sizing accounts were calculated. The
original 408-proposal/512-attempt recipe was preserved, not executed or reduced.

The case harness uses the installed app's provider adapter, signed-in owner,
production ResearchToolkit, supervisor and native worker with one isolated
ResearchStore. It exposes only nine research tools; the general Agent's other
tools are excluded. At most one experiment and eight tool calls per case were
authorized. Confirmation of the historical plan was supplied only within that
existing user authorization. Actual proposals, native receipts and outcomes
remain authoritative. Expectations were not included in the model prompts.

A single corrective continuation was explicitly recorded when the model only
promised action. It was not an unassisted success. The harness also prompts a
status read after its isolated worker completes; production has no equivalent
background LLM wake-up. Original case A and its assisted trace are separate.
Original result artifacts remain unchanged and attempt ceilings were retained.

Private minimal evidence stays under `.agent-native/research-supervisor32/`:
`live-model-evidence/` contains actual tool traces, model responses and independent
native verification; `live-fixtures/manifest.json` records fixture provenance;
`live-model-store/` is the sole isolated store. Credentials are not included.

## Defect fixed during qualification

Before model case calls, all six actual `research_get_run` responses lost
`research_evidence` during optional-report compaction. The preferred field list
contained other aliases but omitted the real key. It now preserves that key
within the existing response cap. The same six-case preflight passes after the
fix. A regression and the affected toolkit suite pass: 20 tests in 0.71 seconds.
The test process also emitted existing shutdown logging-to-closed-stream errors;
those are not a failed financial assertion. This fix does not certify arbitrarily
large nested evidence beyond the current response bounds.

## Remaining work established by these tests

1. Make execution state drive continuation; an assistant saying “I will” must
   not count as a submitted action. Persist bounded model continuation and stop
   explicitly when it cannot act. Repeating prompts is not a production loop.
2. Derive feasible size candidates from unchanged hard limits. In B, a 10% ticket
   under a 1% symbol cap cannot fit even the current quarter-size choice (2.5%).
   Adding capital cannot repair that percentage mismatch.
3. Filter permitted remedies against executable frozen candidates before
   advertising them. E–F are already at the 45-day maximum holding point.
4. Qualify an actually accepted small native case through model-driven exact
   save/reopen after those fixes, and retain rejection cases as regressions.

The general UI capability smoke sent 54,172 input tokens for one tool request
(5.5-second first token); it should not be the default context size for a narrow
research decision. The isolated qualification uses a smaller research-only
surface. This difference is explicit; it does not prove the full UI has been
optimized. No separate API provider was configured and no new full scanner ran.

No further user credentials are needed for these fixes or small tests. Linux
production qualification and public/Git delivery remain separate incomplete gates.
