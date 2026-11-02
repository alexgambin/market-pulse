MARKET PULSE — POSITIONING / COT INTEGRATION
DECISIONS AFTER PHASE 0 REVIEW

Use the following decisions as the agreed specification for proceeding with Phase 1.

The current Market Pulse workspace is the source of truth.

The separate COT Dashboard ZIP is only a reference implementation from which useful
mappings, navigation ideas and visualizations may be reused. Do not port it as a
separate application or preserve its architectural mistakes.

The objective is not to add another dashboard full of COT data.

The objective is to build POSITIONING as a deterministic, auditable and extensible
intelligence layer inside Market Pulse, while preserving the scientific integrity of
the current Validation v2 baseline.

Do not introduce unrelated improvements while implementing this feature.

If implementation reveals a material ambiguity that could affect semantics, historical
reproducibility, look-ahead, forecast generation or validation, stop and ask before
choosing silently.


==================================================
1. CFTC REPORT TYPES
==================================================

Keep the six existing Disaggregated/TFF series currently used by Market Pulse.

Do NOT replace them with Legacy.

Add Legacy only where appropriate and keep the report families explicitly separated.

The internal identity of a positioning series must include at least:

- instrument
- report_type
- participant_category

Report types such as:

- LEGACY
- DISAGGREGATED
- TFF

must remain distinct.

This is important because Disaggregated and Traders in Financial Futures are
structurally different CFTC reports.

Participant categories across Legacy, Disaggregated and TFF must never be treated as
if they represented the same economic actor.

For example, do not directly compare or merge:

Legacy Non-Commercial
vs
TFF Leveraged Funds
vs
Disaggregated Managed Money

as if they were equivalent raw categories.

If a future unified normalized metric is created, each underlying series must first be
normalized independently and retain complete provenance.

The frontend and API must always make report type and participant category
identifiable.


==================================================
2. INSTRUMENT SCOPE — NO SCOPE CREEP
==================================================

Do NOT import the complete 42-instrument universe from the standalone COT Dashboard.

POSITIONING must follow the existing Market Pulse market universe, not expand it.

Initially include only instruments already covered by Market Pulse prices for which a
valid CFTC futures mapping exists.

This may include, after validating the mapping:

- WTI
- Brent, only if an appropriate and defensible CFTC mapping exists
- Gold
- Silver
- currently supported FX currencies/pairs
- BTC
- ETH where applicable
- S&P 500
- Nasdaq
- Dow
- Russell 2000
- DXY if a valid CFTC contract exists
- other CURRENT Market Pulse instruments only when there is a validated corresponding
  CFTC series

Do NOT add:

- agriculture
- livestock
- wheat
- corn
- soybeans
- cattle
- coffee
- cocoa
- sugar
- cotton
- or other markets merely because the ZIP contains them.

Market Pulse previously made an explicit architectural decision not to expand
indicators and sources unnecessarily.

COT integration must not become a backdoor expansion of the asset universe.

The rule should conceptually be:

Market Pulse instrument exists
+
price coverage exists
+
validated positioning source exists
=
POSITIONING can be enabled.

If Market Pulse adds a new priced instrument in the future, POSITIONING should be
designed so its validated COT mapping can then be activated without redesigning the
architecture.


==================================================
3. NORMALIZED POSITIONING METRIC
==================================================

Do not reuse the standalone dashboard's absolute contract thresholds.

Rules such as:

net > 80,000 = strong bullish

are invalid across heterogeneous futures markets.

Use:

normalized_net = net_position / open_interest

as the base normalized positioning metric where Open Interest is available and
semantically appropriate.

Persist the underlying components as well:

- long
- short
- net
- open_interest
- normalized_net

Do not collapse everything into one final score in storage.


==================================================
4. MULTIPLE POSITIONING HORIZONS
==================================================

Do NOT use only a 52-week percentile.

Calculate and persist at least:

percentile_52w

and:

percentile_156w

The interpretations are different.

52 weeks:
TACTICAL POSITIONING

It answers approximately:

"How unusual is current positioning relative to the last year?"

156 weeks:
REGIME CONTEXT

It answers approximately:

"How unusual is current positioning relative to the last three years?"

This matters in markets whose absolute participation and structural positioning evolve
over time.

For example, a large gold speculative net position may appear extreme relative to the
previous 52 weeks while being routine in a multi-year context.

The UI may initially emphasize 52w and display 156w as secondary regime context.

However:

BOTH must be calculated,
BOTH must be persisted,
BOTH must be available through the API.

Store enough underlying history to allow future experimentation with other horizons
such as 260 weeks without reconstructing the raw source.


==================================================
5. COMPLETE WINDOWS — NO SILENT FALLBACKS
==================================================

Require sufficient observations for each percentile window.

If there is insufficient coverage:

return NULL.

Do not silently substitute:

156w → 52w

or:

52w → shorter window.

Do not convert missing values to zero.

Missing means unavailable, not neutral.

DEFINE COMPLETENESS EXPLICITLY.

The CFTC skips or delays weekly releases several times a year for holidays and other
calendar exceptions. A nominal 52-week window will therefore routinely contain fewer
than 52 observations through no fault of the system.

Therefore the specification must state, explicitly and in a versioned form:

- the minimum number of valid observations required for a window to be considered
  complete (for example, a stated minimum count or coverage ratio for 52w and 156w)
- how gaps caused by holidays or delayed releases are treated

A gap must never be silently interpolated, forward-filled or treated as zero.

The completeness policy must be deterministic, documented and versioned alongside the
calculation version (see section 23), so that a later change to the policy does not
retroactively and invisibly alter historical values.

Expose the reason when practical, for example:

INSUFFICIENT_HISTORY
SOURCE_UNAVAILABLE
MISSING_OPEN_INTEREST
STALE_DATA
MAPPING_UNAVAILABLE

The standalone ZIP behavior:

parseFloat(value) || 0

must NOT be reproduced.


==================================================
6. HISTORICAL DATA
==================================================

Download and preserve up to approximately ten years of CFTC history when available and
reasonable.

This is NOT because the primary score is a five-year score.

The purpose is:

- warm-up for rolling calculations
- 52-week analysis
- 156-week analysis
- possible future 260-week analysis
- regime research
- historical visualization
- later empirical testing
- avoiding expensive reconstruction of historical series

Preserve raw source observations separately from derived metrics whenever practical.

Report the expected storage growth (approximate row counts and database size impact)
in the final report, so the operator can decide on backup strategy before the dataset
grows.


==================================================
7. CRITICAL LOOK-AHEAD RULE
==================================================

The main temporal risk in COT is confusing POSITION DATE with INFORMATION
AVAILABILITY.

CFTC data generally describe positions as of Tuesday and are normally published later,
commonly Friday, subject to official calendar exceptions.

Therefore store separately at least:

report_date / as_of_date
published_at
fetched_at

Definitions:

report_date / as_of_date:
Date represented by the CFTC positions.

published_at:
Officially supported publication/release time when it can be established.

fetched_at:
Moment Market Pulse actually received the data.

Never assume that Tuesday positioning was available on Tuesday.

For forecasting research:

report_date != information availability.

Historical data downloaded today do NOT prove that Market Pulse or any market
participant could have known those values on report_date.

If historical published_at cannot be established reliably, use a documented
conservative availability policy.

The policy must be explicit and versioned.

Do not manufacture precise historical publication timestamps when evidence does not
exist.


==================================================
8. UPDATE SCHEDULING
==================================================

Follow the official CFTC release calendar rather than assuming every week follows
exactly the same schedule.

Account for:

- holidays
- delayed releases
- calendar exceptions
- source failures

Use bounded retries.

Also perform a later recovery check so that a temporary failed publication/download is
eventually recovered.

The update process must be:

- idempotent
- observable
- safe to rerun

Do not poll aggressively for low-frequency weekly data.


==================================================
9. SYNTHETIC FX
==================================================

Do NOT reuse the standalone dashboard's method of subtracting raw futures contract
counts between currencies.

Raw contract counts from different futures cannot safely be treated as directly
comparable.

Synthetic FX positioning should instead be based on DIFFERENCES BETWEEN NORMALIZED
SCORES.

Both legs must:

- use compatible normalization
- be temporally aligned
- expose their underlying source series
- retain complete provenance

For example, conceptually:

EUR/JPY synthetic positioning
=
normalized EUR positioning
-
normalized JPY positioning

But implement the exact formula only using compatible normalized inputs.

Mark synthetic observations explicitly, for example:

OBSERVED_CFTC

vs

SYNTHETIC_POSITIONING

Initially synthetic FX should be informational only.

Do NOT automatically translate synthetic values into:

BULLISH
BEARISH
EXTREME_LONG
EXTREME_SHORT

and do NOT initially include them in extreme-positioning rankings.

First accumulate evidence about whether the synthetic differential has useful meaning.


==================================================
10. POSITIONING IS NOT A DIRECTIONAL FORECAST
==================================================

Avoid semantic mistakes such as:

EXTREME_LONG = BULLISH

Positioning describes market structure/crowding.

It is not automatically a directional prediction.

An extreme long condition may simultaneously mean:

strong conviction

and:

high liquidation/reversal vulnerability.

Therefore positioning states should describe POSITIONING, not forecast direction.

The AI may later interpret positioning in combination with:

- narratives
- fundamentals
- price
- macro
- derivatives
- prediction markets

but deterministic POSITIONING calculations themselves must not manufacture a
directional market forecast.


==================================================
11. CRYPTO / DERIVATIVES SCOPE
==================================================

For the initial integration, reuse only the existing BTC/Binance derivatives
infrastructure where appropriate.

Do NOT expand the cryptocurrency universe merely as part of this work.

Do NOT invent a unified "crypto positioning score" yet.

Keep concepts such as:

CFTC positioning
funding
open interest
long/short ratios

as distinct underlying signals.

POSITIONING should be architected so these may eventually coexist, but this phase
should not attempt to solve the entire derivatives architecture.


==================================================
12. FRONTEND INTEGRATION
==================================================

Do NOT preserve the standalone COT Dashboard as a separate server/application.

No iframe.

No separate Express service in the final architecture.

Integrate useful navigation and visualization ideas natively into the existing Market
Pulse frontend, particularly:

static/mercados.html

POSITIONING should feel like a native Market Pulse intelligence layer.

Potential structure:

POSITIONING

Overview
FX
Equities
Energy
Metals
Rates
Crypto

Only show categories actually supported by the current Market Pulse universe.

Do not add AGRICULTURE merely because it exists in the reference ZIP.

Useful overview fields may include:

Instrument
Report Type
Participant Category
Net
Net/OI
Weekly Change
52w Percentile
156w Percentile
Positioning State
Freshness

Detailed views may include historical positioning charts and the useful visualization
patterns from the ZIP, adapted to the main application.

Do not reproduce the ZIP's invalid absolute thresholds.


==================================================
13. AI COST AND TOOL BEHAVIOR
==================================================

POSITIONING calculations must be deterministic.

Do NOT introduce AI calls to:

- calculate scores
- calculate percentiles
- classify positioning
- build synthetic FX
- update CFTC data

These belong in Python/data logic.

However, a deterministic agent tool still consumes AGENT ITERATION BUDGET when
invoked.

This matters because current measured Market Pulse behavior shows:

- approximately $3.6-$3.7/day of known AI cost at the initial measured run rate
- some agent passes already approaching MAX_ITERACIONES=15
- the manual pass used 15 iterations
- the 10:00 scheduled pass used 12 iterations

Therefore do NOT impose the artificial rule:

"POSITIONING must result in exactly the same number of AI calls."

Instead, design the future tool so it is contextually relevant.

The agent should only have reason to invoke positioning when the active
narrative/forecast concerns an instrument for which valid positioning exists.

It should not query positioning indiscriminately during every pass.


==================================================
14. IMPORTANT: DO NOT CHANGE THE CURRENT V2 BASELINE
==================================================

Although the POSITIONING engine, database, API and frontend can be implemented now, do
NOT expose the new positioning tool to the current forecasting agent yet.

Use a feature flag such as:

POSITIONING_AGENT_ENABLED=false

The existing COT behavior already available to the agent should remain unchanged.

CRITICAL IMPLEMENTATION REQUIREMENT FOR THE FLAG:

While POSITIONING_AGENT_ENABLED is false, the positioning tool MUST NOT appear in the
tool schema sent to the model API.

It is NOT sufficient for the tool to be registered and merely return an error or an
empty result when invoked.

Reason: adding a tool definition to the agent's tool list changes the payload the model
receives on every single call. That is a change to the forecast-generating system even
if the tool is never invoked once. It could alter model behavior, token usage and
therefore the baseline itself.

While the flag is off, the tool definitions sent to the model must remain equivalent to
the current baseline. Verify this explicitly with a test (see section 27).

Reason for the overall flag:

Validation v2 has just begun accumulating clean independently validated forecasts.

Adding a new source/tool to the forecasting process now would change the
forecast-generating system and contaminate the baseline.

Implement the infrastructure now.

Activate the new agent capability later as a deliberate experiment.


==================================================
15. ENGINE VERSIONING
==================================================

A future change that enables POSITIONING for forecasting should receive a descriptive
engine version.

Do not rely only on labels such as:

v2
v3
v4

Those may remain convenient human-facing labels in the dashboard, but they are
insufficient as scientific provenance.

Prefer descriptive identifiers such as:

MP-0.9-baseline

MP-0.10-positioning

or another consistent semantic/versioning scheme.

The exact naming convention can follow existing project conventions.

The important requirement is:

the version must identify what changed.

When a new forecasting engine version is deployed to test POSITIONING, that release
must contain ONE meaningful experimental change:

POSITIONING availability.

Do NOT simultaneously modify:

- forecasting instructions
- probability guidance
- tone
- prompt structure
- model
- tool behavior unrelated to POSITIONING
- prediction registration semantics
- other information sources

unless absolutely necessary for the POSITIONING experiment and explicitly documented.

If:

MP-0.10-positioning

means:

"POSITIONING + three unrelated prompt improvements"

then any later Brier improvement cannot be attributed to positioning.

Protect experimental isolation.


==================================================
16. ENGINE VERSION IS NOT THE EXPERIMENTAL UNIT
==================================================

This is critical.

Do NOT assume that:

prediction created under MP-0.10-positioning

means:

POSITIONING was actually used for that prediction.

These are different statements:

"The agent had access to POSITIONING."

"The agent actually consulted POSITIONING when producing this forecast."

A forecast about an ECB decision, for example, may be generated under the
positioning-enabled engine without ever invoking the positioning tool.

Therefore:

Brier(v2) vs Brier(v3)

or:

Brier(MP-0.9-baseline) vs Brier(MP-0.10-positioning)

is useful as a coarse dashboard view but is NOT sufficient as the scientific
comparison.


==================================================
17. RECORD ACTUAL TOOL EXPOSURE PER PREDICTION
==================================================

Reuse the architectural pattern already implemented for:

consultas_biblioteca

That system correctly distinguishes:

"the agent could consult the library"

from:

"the agent actually consulted the library during this reasoning chain."

Apply the same principle to POSITIONING.

For every prediction, preserve an external association describing the tools/data
actually used during the reasoning chain that produced it.

Prefer a general solution over a hard-coded boolean.

For example, conceptually:

prediction_tool_usage

- prediction_id
- tool_name
- invocation_id
- invoked_at
- instrument
- source/report type where relevant
- data snapshot/reference
- engine_version
- metadata

or reuse/extend an existing generic tool-observation mechanism if one already exists.

A derived convenience field such as:

uso_positioning = true/false

may be exposed through the API/dashboard.

But the underlying source of truth should preferably record the actual tool
invocations.

Do not add mutable fields directly to the immutable prediction contract if an external
association can represent this correctly.


==================================================
18. AVAILABLE DATA VS EXPOSED DATA VS USED DATA
==================================================

For forecasting research, distinguish at least three concepts.

A. AVAILABLE

What POSITIONING information objectively existed and was available to Market Pulse when
the forecast was created?

B. EXPOSED

What positioning capability/data was available to the model/tool environment during
that forecasting execution?

C. USED

What positioning information/tool was actually consulted in the reasoning chain that
produced the prediction?

These must not be conflated.

For example:

COT data existed
≠
agent had access to it
≠
agent consulted it
≠
it necessarily influenced the forecast.

Where practical, preserve enough external associations to reconstruct these
distinctions later.

Do not modify the immutable prediction contract itself.


==================================================
19. REAL SCIENTIFIC COMPARISON
==================================================

The eventual comparison should not simply be:

Brier(v2) vs Brier(v3).

It should support analyses such as:

Brier(predictions where positioning was actually consulted)

vs

Brier(predictions where positioning was not consulted)

and ideally stratify by:

- instrument
- asset class
- forecast horizon
- narrative
- probability band
- report type
- positioning state
- temporal regime
- engine version

This gives substantially more analytical power.

However, do NOT interpret this naive observational comparison automatically as causal
evidence.

The agent is more likely to query POSITIONING precisely in situations where positioning
is relevant, unusual or uncertain.

Therefore:

used_positioning=true

is a selected cohort, not a randomized treatment group.

Preserve the data now so better matched/controlled analyses can be performed later.


==================================================
20. TEMPORAL REGIME / CORRELATION PROBLEM
==================================================

Forecasts are not independent observations.

Fifteen predictions related to the same Iran/oil thesis are not equivalent to fifteen
unrelated experiments.

Similarly, comparing a baseline period with a positioning-enabled period can be
misleading if market regimes differ.

For example:

baseline period:
quiet markets

positioning-enabled period:
major Fed repricing + geopolitical shock

A Brier difference could reflect regime rather than POSITIONING.

Therefore preserve enough metadata to later analyze:

- narrative/thread clustering
- instrument
- forecast horizon
- time period
- market regime
- actual tool usage

The prediction-level tool usage record is particularly valuable because it permits
comparisons WITHIN overlapping calendar periods after POSITIONING is enabled.

Do not claim causal improvement from raw before/after Brier alone.


==================================================
21. FUTURE EXPERIMENTAL DESIGN
==================================================

Do not implement a complex A/B framework unless required now.

But design persistence so that a future experiment could compare:

CONTROL:
agent has existing tools but POSITIONING unavailable

TREATMENT:
same model
same prompt
same context
same forecast contract
POSITIONING available

The only intended difference should be the positioning capability.

If such an experiment is later implemented, context freezing and identical forecast
contracts will be important.

For now the priority is to preserve the necessary provenance.


==================================================
22. FORECAST CONTEXT ASSOCIATIONS
==================================================

For new forecasts, support external associations that can eventually answer:

- What market data was objectively available?
- What COT release was available?
- What POSITIONING snapshot existed?
- What POSITIONING metrics were exposed to the agent?
- Did the agent actually call the positioning tool?
- Which series did it request?
- Which report type?
- Which participant category?
- Which calculation version?
- Which values were returned?
- Which engine version generated the forecast?

These associations must not mutate the immutable prediction contract.

Reuse existing architectural patterns whenever possible rather than creating a parallel
bespoke system.


==================================================
23. CALCULATION VERSIONING
==================================================

POSITIONING formulas must be deterministic, reproducible and versioned.

Persist a calculation version such as:

positioning_calc_version

or equivalent.

The window-completeness policy defined in section 5 forms part of this calculation
version.

If the formula or the completeness policy changes later, historical results must remain
reproducible.

Do not silently recalculate historical stored metrics using a new formula without
preserving which version generated each value.


==================================================
24. RAW DATA AND PROVENANCE
==================================================

Preserve enough provenance to audit a positioning observation.

At minimum, where applicable:

- source
- instrument
- CFTC market/contract identifier
- report_type
- participant_category
- report_date
- published_at
- fetched_at
- long
- short
- net
- open_interest
- normalized_net
- percentile_52w
- percentile_156w
- observed/synthetic flag
- calculation version

Avoid duplicate truth.

If raw observations and derived observations belong in separate tables, prefer that
design.


==================================================
25. STALENESS
==================================================

POSITIONING is low-frequency data.

The UI/API should expose freshness explicitly.

Do not make a weekly COT observation look like live market data.

A user should be able to understand:

Positions as of:
Tuesday X

Published:
Friday Y

Market Pulse received:
Friday Y at HH:MM

Freshness:
CURRENT / STALE / etc.

Use deterministic staleness rules.


==================================================
26. EXISTING COT SUMMARY
==================================================

Do not break or silently change the existing COT summary/tool currently used by Market
Pulse.

POSITIONING is an extension.

The existing agent behavior must remain stable during the Validation v2 baseline. This
includes the tool-schema requirement in section 14.

If refactoring shared internals is necessary, preserve observable behavior and cover it
with regression tests.


==================================================
27. TESTING
==================================================

Add comprehensive mocked tests.

At minimum test:

- CFTC mapping
- Legacy parsing
- Disaggregated parsing
- TFF parsing
- participant-category separation
- missing values remain NULL
- Open Interest handling
- normalized_net
- 52-week percentile
- 156-week percentile
- incomplete windows
- WINDOW GAPS: a percentile window containing gaps caused by holidays or delayed
  releases (for example 50 valid observations where 52 are nominally expected) must
  apply the documented completeness policy from section 5 deterministically. It must
  NOT silently interpolate, forward-fill, treat gaps as zero, or fall back to a shorter
  window.
- synthetic FX alignment
- synthetic provenance
- no raw-contract subtraction
- report_date vs published_at vs fetched_at
- holiday/delayed release behavior
- idempotent ingestion
- bounded retries
- staleness
- calculation versioning
- API output
- frontend data contract
- agent feature flag OFF
- TOOL SCHEMA ISOLATION: with POSITIONING_AGENT_ENABLED=false, assert that the tool
  definitions sent to the model API do not contain the positioning tool. This test
  protects the Validation v2 baseline and must fail loudly if the tool leaks into the
  schema.
- no forecasting behavior change while OFF
- prediction/tool association
- multiple tool calls associated with one prediction
- no accidental mutation of immutable prediction fields

No tests should depend on live CFTC/Binance/network availability.


==================================================
28. WHAT TO REUSE FROM THE COT ZIP
==================================================

Potentially reuse:

- validated instrument mappings after rechecking them
- useful category/navigation concepts
- useful chart concepts
- historical visualization ideas
- frontend interaction patterns

Do NOT reuse:

- separate Express server architecture
- missing → zero behavior
- absolute net thresholds
- simplistic bullish/bearish classification
- raw-contract synthetic FX subtraction
- anything that conflicts with current Market Pulse architecture


==================================================
29. PRODUCT DIRECTION
==================================================

POSITIONING is intended to become one component of a broader Market Pulse / future
Synapsedge intelligence architecture:

NEWS
+
MACRO
+
PRICES
+
POSITIONING
+
DERIVATIVES
+
NARRATIVES
+
FORECASTS
+
PREDICTION MARKETS
+
INDEPENDENT VALIDATION

The purpose of POSITIONING is therefore not merely visualization.

Long term we want to be able to test empirically questions such as:

Does extreme positioning improve or worsen forecast quality?

Does narrative + positioning convergence improve Brier?

Do crowded positions increase reversal risk?

Does COT information improve energy forecasts more than FX forecasts?

Does 52w positioning matter more at short horizons?

Does 156w regime positioning matter more at longer horizons?

Does actual use of POSITIONING improve forecasts relative to otherwise comparable
forecasts?

The architecture built now should make these questions answerable later.


==================================================
30. IMPLEMENTATION PRIORITY
==================================================

Proceed incrementally.

Suggested order:

PHASE 1
Architecture + schema + provenance + versioning.

PHASE 2
CFTC adapters and deterministic positioning engine.

PHASE 3
Historical persistence + scheduler/update/recovery.

PHASE 4
API.

PHASE 5
Prediction-context/tool-usage associations, reusing the consultas_biblioteca pattern
where possible.

PHASE 6
Agent tool implementation behind:
POSITIONING_AGENT_ENABLED=false
(including the tool-schema exclusion required by section 14)

PHASE 7
Native frontend integration in Market Pulse.

PHASE 8
Tests and regression verification.

NOTE ON ORDERING: the tool-usage association layer (Phase 5) deliberately comes BEFORE
the agent tool itself (Phase 6). Building the recording layer first means the tool is
instrumented from birth and does not need to be revisited to add provenance. Since the
agent tool ships disabled anyway, there is no urgency in reaching it first.

Do not enable POSITIONING for live forecast generation during this implementation.

At the end, report:

1. files created
2. files modified
3. schema changes
4. migrations
5. API endpoints
6. scheduler behavior
7. CFTC sources/report types supported
8. instruments supported
9. formulas implemented
10. window-completeness policy adopted
11. historical coverage
12. expected storage growth (approximate row counts and database size impact)
13. frontend changes
14. agent tool behavior
15. feature-flag behavior, including proof that the tool is absent from the model tool
    schema while disabled
16. prediction/tool provenance implementation
17. tests added
18. tests passed
19. any unresolved limitations
20. any assumptions that should be reviewed before POSITIONING_AGENT_ENABLED can ever
    be set to true

Most importantly:

Do not optimize for having more data.

Optimize for scientifically usable data.

Do not optimize for making POSITIONING look predictive.

Build the infrastructure that will allow Market Pulse to determine whether it actually
is predictive.