# Asset-aware research journeys — proposed product specification

Research date: 10 September 2026. This is a proposed design, not an assertion of shipped support. Current product scope remains one to eight **long NSE cash-equity CSV signal strategies**, as stated in `docs/research/BRIEF.md`. Broader engine capability does not enable an asset class through our adapter automatically.

## 1. Product rule: select the research intent; resolve the instrument precisely

The user should start with **what they want to test** (signals, rule strategy, portfolio allocation, spread/hedge, execution assumptions), then choose or import the instruments. A reusable **Instrument Profile** resolves venue, segment, canonical instrument identity, currency and dated contract terms. "Gold", "Nifty" and "EURUSD" alone are insufficient: these can identify a reference price, an ETF, a cash holding, an expiring derivative or a perpetual contract with different accounting.

The user should not choose a database or guess a history endpoint. OpenAlgo owns connected-provider access and the native data archive; our planner requests only the data required by the selected strategy and its whole optimization space. The planner must first establish that OpenAlgo's provider and archive can represent that data. The current OHLC archive is not automatically an option-chain, quote, corporate-action or funding-rate store. Extend the native data services explicitly where needed; do not silently introduce another downloader or put unlike data into candle rows.

The automatic layer has three different responsibilities:

| Category | Product behavior | Examples |
|---|---|---|
| Authoritative facts | Resolve automatically with source, effective dates and version; inspect on demand | Contract ID, expiry timestamp, multiplier, tick/lot size, settlement currency, trading sessions, corporate-action event, fee schedule when available historically |
| Derived requirements | Compute automatically from all inputs and ranges | Price interval, warmup, expiries and strikes potentially selected, benchmark/conversion series, cross-session holding, funding timestamps, all legs and hedge instruments |
| Consequential assumptions | Show a compact summary, allow deliberate editing, freeze with the run | Fill convention, slippage, borrow approximation, cash availability, reinvestment, expiry exit/roll rule, assignment policy, missing-data handling, benchmark and validation allocation |

Never present an assumed historical fee, borrow availability, margin schedule, fill sequence or funding series as automatically verified. A missing fact that affects validity produces a focused **Needs input** or **Unsupported by this connection/engine** state. A user-chosen approximation is a separate named model with affected results labelled accordingly; it is not a silent fallback.

Nautilus's official instrument model illustrates why this belongs at instrument level: separate equity, pair, future, option and perpetual types carry different identities and terms. This is a design reference, not certification of our pinned package or adapters. [Nautilus instrument concepts](https://nautilustrader.io/docs/latest/concepts/instruments/)

## 2. Common fields that belong in the setup, irrespective of class

**Primary selections:** research name/question; signal or strategy source; market/universe; period; capital and account base currency; direction; sizing/allocation; trading and holding rules; benchmark; the period reserved for validation; objective and constraints when optimizing.

**Automatically prepared:** available instrument choices from connected providers; canonical symbol mapping and effective dates; data frequency inferred from timestamps and execution semantics; full candidate window and applicable warmup; native archive coverage; missing acquisition windows; provider access/retention checks; required calendar/FX/metadata streams; engine capability match; estimated job size before starting; a frozen evidence snapshot shared across trials.

**Visible assumptions drawer:** same-bar stop/target priority; next eligible execution time after a signal; open/close/quote/limit-fill convention; latency/spread/slippage model; commission and other modeled costs; corporate-action basis; settlement and cash reuse; annualization/risk-free settings for metrics; sparse/missing observation policy. Defaults can be usable, but must not conceal a changed economic meaning.

**Data quality review:** state required/covered/absent for each data type; show affected instruments, signals and dates; resolve ambiguous symbols in place; never manufacture prices; do not forward-fill a missing executable price and then claim a fill. Inactivity, suspension, not-yet-listed contracts, expired symbols and provider failures are different states.

**Persistent context:** save the chosen profile and assumptions in each immutable setup revision. A rerun using later metadata or broader data coverage creates another run; an exact replay preserves the original evidence. Portfolio combinations must share a coherent valuation timeline and distinguish stale marks from possible execution times.

## 3. Cash equities, ETFs and listed funds

**Journey:** import signals or pick a dated universe → set long holding/allocation rules → choose price return or total-return treatment → run → investigate trades, concentration, dividends and liquidity → compare a baseline/benchmark → save or combine strategies.

| Responsibility | Required design |
|---|---|
| User chooses | Exchange/region and instrument or dated universe; cash long versus margin/intraday product; account currency; share/notional/weight sizing; holding rules; dividend reinvestment choice; benchmark; rebalancing for allocation studies |
| Auto-resolve | Security/ISIN identity, symbol history, listing/delisting dates, exchange timezone/sessions, dated tick/lot rules, traded currency, split/consolidation events and distributions where authoritative data exist |
| Visible assumptions | Raw versus adjusted signal/valuation basis; cash dividends and their timing; dividend reinvestment; fractional shares; settlement cash reuse; auction participation; transaction costs and liquidity participation |
| Historical data | Daily or intraday OHLCV at required frequency; quotes/trades when a quote-based execution claim requires them; splits and distribution events; point-in-time universe constituents; FX for account conversion; benchmark on comparable return basis |
| Engine/adapter requirement | Ordinary cash long can use the present VectorBT path within the current supported scope. Total-return cashflows, corporate actions, foreign currency and point-in-time universe support require explicit extension and regression evidence. ETF trading prices cannot be replaced silently by underlying NAV. A bond ETF remains a listed fund position, not a directly held bond cashflow schedule. |
| Stop/needs-input cases | Unresolved exchange/symbol; adjusted history of unknown basis; missing action data over a relevant holding; unavailable delisted history; current constituent list offered as historical universe; insufficient executable data; unmodeled settlement or auction requirement |

Cash-action treatment is not interchangeable with derivative contract adjustment. NSE documents derivative adjustments to strike, position and multiplier, including dated implementation of actions; it demonstrates the need for event-aware metadata. [NSE corporate-action adjustments](https://www.nseindia.com/static/products-services/equity-derivatives-corporate-actions-adjustments)

## 4. Equity shorts and market-neutral portfolios

**Journey:** choose a short/long-short strategy → distinguish intraday closeout from overnight borrow → assess lendable coverage and financing → run with explicit borrow assumptions → inspect gross/net exposure, borrow expense and forced exits → validate stress and concentration.

| Responsibility | Required design |
|---|---|
| User chooses | Intraday short versus borrow-backed overnight short; long/short gross and net caps; borrow constraint/approximation policy; pair or basket relationships; hedge sizing and rebalance rules |
| Auto-resolve | Venue and broker product availability; closing session and documented square-off profile when applicable; available historical borrow/fee observations; margin/collateral metadata with effective dates |
| Visible assumptions | Borrow availability and its historical limitations; borrow fees/rebates, recalls and buy-ins; dividends owed on short holdings; collateral haircuts; intraday compulsory closeout; margin call/liquidation rules |
| Historical data | Cash-equity data plus borrow availability/fees, short dividends, historical margin/financing inputs; simultaneous price coverage for both sides |
| Engine/adapter requirement | Short signals in a general VectorBT API are not enough to claim faithful borrow-backed trading. Need validated financing, collateral and forced-exit accounting. Current adapter is long-only and must reject shorts until extended. |
| Stop/needs-input cases | Overnight short requested with only cash candles and no selected borrow model; borrowed security unavailable; pair leg absent; historical position requires a capability the selected engine/adapter cannot reproduce |

IBKR exposes shortable availability and stock-loan rate history as separate financing data, illustrating that price candles do not supply those facts. [IBKR securities financing](https://www.interactivebrokers.com/en/trading/securities-financing.php?menu=A), [Short availability](https://investors.interactivebrokers.com/en/trading/short-securities-availability.php)

## 5. Equity/index options and multi-leg strategies

**Journey:** choose underlying and options strategy/template or legs → define historical contract selection (expiry/DTE, strike/moneyness/delta) → set net entry/exit and hedge rules → validate all required chains/legs → backtest → inspect payoff and path-dependent realized results separately → analyze Greeks, expiry and leg attribution → optimize only meaningful conditional parameters → compare/validate.

| Responsibility | Required design |
|---|---|
| User chooses | Underlying and venue; single leg/spread/straddle/strangle/covered or hedged structure; long/short and ratios; exact contract versus dynamic selection; expiry/DTE rule; strike/moneyness/delta/premium rule; per-leg versus package entry/exit; roll/adjust/hedge rules; close-before-expiry versus carry |
| Auto-resolve | Historical listed option series, strike/right/expiry timestamps, multiplier and adjusted deliverable, exercise style, settlement type and fixing, permitted size increments, sessions; map every leg to the dated contract; resolve all contracts any optimization candidate could select |
| Visible assumptions | Net-price/package fill versus independently filled legs; fill/quote freshness and maximum leg delay; IV/Greeks source and model conventions; risk-free/dividend assumptions where Greeks are calculated; exercise and assignment model; handling resulting underlying/futures positions; portfolio margin model; hedge execution; treatment of partial leg failure |
| Historical data | Historical option chain/instrument definitions; each candidate leg's executable quotes or appropriately bounded bars; underlying and hedge prices; expiry fixing/settlement data; adjustment events; IV/Greeks when selection/risk needs them, or explicitly modeled Greeks inputs. Historical chain selection cannot be derived from today's chain. |
| Engine/adapter requirement | Need a tested multi-leg execution/valuation, margin and lifecycle adapter. VectorBT can analyze an input price series but is not automatically a complete options lifecycle model. Latest Nautilus documents option instruments and chain workflows; availability in our pinned engine and native-data path must be separately tested. |
| Stop/needs-input cases | Broker lacks expired contracts or chain history; ATM/delta selection impossible from supplied observations; one leg stale/missing; American early exercise unmodeled; cash-settled assumption applied to physical delivery; contract adjustment lost; required margin interactions unsupported |

**Extra result tabs:** leg and package trades, realized P&L versus expiry payoff, Greek exposure through time, expiry/assignment events, hedge contribution, spread/slippage cost, margin utilization and stress scenarios. A payoff diagram is not historical execution proof. A theoretical option price is a separately labelled modeled-price experiment, never silently equivalent to the user's broker history.

NSE securities options use CE/PE contract identity and dated expiry/lot specifications. US standard equity options documented by OCC use American exercise and share delivery, with adjusted contracts after some actions. Therefore region/venue cannot be ignored. [NSE securities derivatives](https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities), [OCC equity option specifications](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications), [OIC exercise](https://www.optionseducation.org/optionsoverview/exercising-options)

Latest Nautilus option-chain documentation requires per-contract quotes and Greeks/instruments in its catalog; an engine run itself does not fetch a missing catalog. Our broker/native archive preparation therefore remains a separate necessary layer. [Nautilus options](https://nautilustrader.io/docs/latest/concepts/options/)

**Source caution:** NSE's generic settlement overview includes older generalized cash-settlement wording. Do not derive production settlement types from that overview alone. Use effective-dated contract specifications and circulars, and fail metadata conflicts instead of guessing. [NSE physical-settlement FAQ circular](https://nsearchives.nseindia.com/content/circulars/INSP38433.pdf)

## 6. Dated futures: equity/index and other financial futures

**Journey:** select underlying contract family → choose exact contract or roll rule → define signal series versus traded contracts → run → inspect each expiry, roll trades/costs, daily settlement and collateral → compare alternative rolls → validate beyond the original period.

| Responsibility | Required design |
|---|---|
| User chooses | Contract family/venue; exact expiry or rule-based nearby contract; roll trigger (calendar/volume/open interest or another declared rule); roll timing; sizing in lots/risk/notional; carry or mandatory close before expiry; collateral and account model |
| Auto-resolve | Actual historical contracts; expiry/last trade/settlement timestamps; multiplier, tick/lot, quote and settlement currency; session/calendar; settlement convention; exchange event changes |
| Visible assumptions | Continuous-series construction/adjustment used for signals; explicit conversion to actual execution contracts; volume/OI trigger observation timing; roll-spread execution and costs; daily MTM cash treatment; initial/maintenance margin and liquidation policy |
| Historical data | Individual expiring contract prices; volume/OI if required by the rule; official settlements; dated contract master; both old and new contract coverage around a roll; historical margin and FX inputs when used |
| Engine/adapter requirement | Validated futures multiplier/MTM/margin/expiry accounting and actual roll trades. A back-adjusted synthetic price series cannot be executed as if it were a continuously tradable security. Need a derivatives adapter; not supported by current cash-equity adapter. |
| Stop/needs-input cases | Only a continuous chart is available but actual fill prices are claimed; missing old contract/roll overlap; ambiguous expiry rule; absent settlement record for carry; margin model mismatch; delivery required but unsupported |

CME distinguishes expiration management from continued exposure through rolling; its rolling indices have a deliberate construction methodology. Product design implication: store the signal-series recipe separately from the contract execution record. [CME expiration and roll](https://www.cmegroup.com/education/courses/micro-e-mini-futures/managing-micro-e-mini-futures-expiration), [CME rolling indices](https://www.cmegroup.com/market-data/cme-group-rolling-futures-indices.html)

## 7. Currency futures/options versus spot FX and CFDs

These require separate profiles even when their visible ticker is the same currency pair.

| Profile | User selections | Automatic facts/data | Visible assumptions / capability gate |
|---|---|---|---|
| Exchange currency futures | Venue, pair, expiry/roll, lots, account currency | Futures specification, contract units/quotation, sessions, daily/final settlement series, both currencies and FX conversion chain | All dated-futures requirements; never substitute spot candles for exchange futures prices or assume quote currency equals P&L settlement currency |
| Currency options | Currency-future/spot underlying identity, legs, expiry/strike rules, hedge | Full derivative chain/contract terms and settlement source | All options lifecycle requirements plus quotation and currency-conversion conventions |
| Broker spot FX / rolling leveraged FX | Broker market, pair, units, direction, sizing/leverage, execution window | Broker bid/ask data and quote precision; timezone/DST; historical financing schedules if available; account conversion rates | Spread/quote-side fills, rollover cutoff and holiday effects, long/short financing, margin/stop-out, settlement convention. Need a validated FX cash/margin adapter. |
| CFD | Exact broker product and reference underlying, size/leverage | Broker-specific contract units, sessions and price stream; financing/dividend adjustment history where supplied | A CFD is not interchangeable with an owned share, future or spot commodity. Counterpart cashflows and broker pricing must be modeled by a capability-tested adapter. |

Needs input if only midpoint prices exist for a spread-based claim, funding is missing across overnight holds, historical account conversion is absent, or the broker does not offer the selected product/history through OpenAlgo. No fallback from currency futures to spot FX to make the run complete.

NSE identifies distinct daily and final currency-derivative settlement prices. OANDA's financing documentation separately describes rollover-related daily cashflows that vary by instrument and time. [NSE currency settlement](https://www.nseindia.com/static/products-services/currency-derivatives-settlement-price), [OANDA financing](https://www.oanda.com/us-en/trading/financing-fees/)

## 8. Commodity futures/options and spot commodities

**Journey:** identify the specific exchange product and unit → choose expiry/roll or option structure → set permitted holding relative to delivery/tender → confirm relevant trading session → run → inspect overnight gaps, rolls, settlement/delivery exclusions and collateral.

| Responsibility | Required design |
|---|---|
| User chooses | Exact product/venue (gold standard/mini etc. are separate contracts); futures/options/spot exposure; lots; roll rule; holding windows; delivery-avoidance policy; options that settle into underlying futures versus other settlement |
| Auto-resolve | Contract unit/quote basis and multiplier; applicable dated specs; day/evening sessions and holidays; last trading day, tender/notice/delivery dates; settlement type; price limits and any dated changes |
| Visible assumptions | Close/roll ahead of delivery; policy for limit-locked markets; session-spanning signals; liquidity/fill model; storage/delivery economics only if explicitly in scope; option exercise creating futures exposure; negative-price compatibility where relevant |
| Historical data | Actual contract prices and settlements; roll overlap; volume/OI when selected; delivery/calendar metadata; option chain/underlying data for options; relevant FX conversion |
| Engine/adapter requirement | All futures/options requirements plus unit conversion and product-specific lifecycle support. Physical inventory, quality, warehouse, transport and delivery workflows should remain deferred. An explicit close-before-delivery backtest can be a bounded supported profile once tested. |
| Stop/needs-input cases | Unknown product variant or quote multiplier; mandatory delivery exposure enters an unmodeled period; missing evening-session history; signed/negative prices rejected by an adapter needed for the historical sample; inherited equity calendar |

MCX maintains different gold variants and effective contract-specification versions. This is why metadata must be contract- and date-specific, not one fixed "commodity" settings template. [MCX gold contracts](https://www.mcxindia.com/en/products/bullion/gold)

## 9. Crypto spot, dated futures, linear and inverse perpetuals

| Profile | User choices | Auto-resolve | Visible assumptions, history and capability requirements |
|---|---|---|---|
| Spot | Venue and pair; account/valuation currency; cash-only or borrow-backed margin; size and fee tier | Base/quote assets, step/tick/minimum notional, symbol life, sessions/maintenance, venue product flags | Venue-specific prices/quotes; maker/taker assumptions and fee currency; dust rounding; conversions. Margin spot adds borrow and liquidation models. Exchange-traded exposure excludes on-chain gas/staking unless explicitly modeled. |
| Linear perpetual | Exact venue contract; long/short, leverage, isolated/cross account model, collateral, trigger-price conventions | Contract unit, quote/settlement currency, funding timestamps/rates when historical feeds exist, maintenance tiers and mark/index series if available | Need traded prices plus funding events, mark/index for claimed liquidation/trigger behavior, dated account rules and costs. Stablecoin collateral is not automatically risk-free or identical to fiat cash. |
| Inverse perpetual/future | Exact inverse contract; collateral asset; reporting currency; margin model; expiry/roll for dated product | Inverse contract term, multiplier, settlement/collateral asset, funding or expiry metadata | Reciprocal-price P&L, collateral valuation and currency conversion must be accounted for in engine adapter; linear return × leverage is not equivalent. Same funding/mark/maintenance requirements as relevant. |
| Crypto options | Underlying and venue, legs, expiry/strike/delta rules, linear/inverse settlement | Contract specs, settlement conventions, chain/instruments and quotes | Same multi-leg/Greeks/lifecycle gates as options, plus crypto currency and collateral models. |

**Extra result views:** funding P&L versus trading P&L, collateral/maintenance through time, liquidation events, currency attribution and venue outage/staleness intervals. A 24/7 market must not inherit a 252-session assumption automatically. Fixed funding schedules and present-day fee tiers must not be projected backward as historical facts.

Needs input or blocked when funding/mark data are absent for leveraged/perpetual claims, contract linearity is unresolved, cross-margin interactions unsupported, or the chosen OpenAlgo connection cannot supply required history. Candle-only exploratory unlevered returns may be a separately labeled profile; it must not be described as a faithful perpetual account simulation.

Binance documents funding history as a distinct market-data endpoint. Bybit explains separate mark-price and inverse settlement mechanics. These are examples of venue-specific inputs, not a decision to add direct venue downloaders. [Binance funding history](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Get-Funding-Info), [Bybit inverse contracts](https://www.bybit.com/en/help-center/article/Inverse-Contract-FAQ), [Bybit mark and inverse rules](https://www.bybit.com/en/contract-rules?type=futureInverse)

## 10. Fixed income, bonds, bills and structured debt — separately deferred

**Why separate:** direct holdings have dated cashflows and price conventions. A bond ETF can follow a listed-fund workflow; it does not validate a direct-bond engine.

| Responsibility | Required design |
|---|---|
| User chooses | Exact security/ISIN; face-value exposure; maturity/coupon preferences or a dated universe; buy/hold/rebalance; reinvestment; credit/rate benchmark and base currency |
| Auto-resolve | Issue/maturity, coupon schedule and rate/reset, day count, clean/dirty price convention, settlement calendar, face value/minimum lot, call/put/amortization events, accrued interest |
| Visible assumptions | Executable spread/liquidity and quote staleness; reinvestment rate; clean-versus-total-return display; treatment of calls, default and recovery; cashflow reinvestment; accrued-interest settlement |
| Historical data | Executable prices/quotes or clearly identified valuation prices; coupon/reset/redemption events; accrued interest and dated terms; curves if risk/valuation models require them; credit events; FX |
| Engine/adapter requirement | Purpose-built cashflow/rate-convention adapter or a suitable established fixed-income engine connector; test ordinary, callable/floating and default cases separately. Do not coerce these into cash-share orders and call the result complete. |
| Stop/needs-input cases | Clean price treated as all-in purchase cost; missing coupons/events; a present-day yield used historically; sparse indicative marks treated as guaranteed executable fills; unsupported callable/structured payoff |

RBI's government-securities guide distinguishes quoted clean prices from accrued-interest-inclusive dirty prices and describes bond cashflow/day-count conventions. [RBI government-securities guide](https://m.rbi.org.in/commonman/english/scripts/FAQs.aspx?Id=711)

## 11. Mutual funds, SIP and periodic allocations — separately deferred

**Journey:** select exact scheme/plan/option → choose lump sum/SIP/rebalance/redemption → set contribution timing and cashflow goals → simulate actual applicable NAV dates → review corpus, money-weighted/time-weighted returns and cost/holding effects.

| Responsibility | Required design |
|---|---|
| User chooses | Scheme and direct/regular plan, growth/distribution option; contribution/redemption schedule; amount and target allocation; distribution reinvestment; benchmark |
| Auto-resolve | Exact scheme identifier and inception/merger lineage; published historical NAV; business calendar; applicable scheme cutoffs, minimum investments and redemption terms when dated and authoritative |
| Visible assumptions | Order acceptance/funds-realization time, missed installments, loads/lock-ins, settlement cash timing, reinvestment of distributions, expense basis already embedded in NAV |
| Historical data | Official NAV and distribution history; dated scheme terms/events; actual or modeled contribution timing; benchmark; no fabricated minute candles |
| Engine/adapter requirement | Contribution/withdrawal and NAV-applicability adapter; correct XIRR versus time-weighted performance; avoid double-counting fund expenses already in NAV. Fund holdings analysis is another data problem. |
| Stop/needs-input cases | Scheme plan/option ambiguous; same-day NAV claimed although known only after the decision cutoff; input asks intraday stop fills on a NAV product; missing scheme history after mergers |

AMFI describes prospective NAV allocation, business-day cutoffs and funds-realization conditions. [AMFI NAV guide](https://www.amfiindia.com/investor/knowledge-center-info?zoneName=NetAssetValueNAV), [AMFI applicable-NAV rules](https://www.amfiindia.com/Themes/Theme1/downloads/NewRuleonApplicableNAVeffectivefromFebruary12021.pdf)

## 12. Additional categories are explicit, not silently bundled

- **Reference indices and synthetic spreads:** useful as signals/benchmarks, not always directly tradable. Require an explicit traded instrument or portfolio transformation.
- **REITs/InvITs and other listed income securities:** reuse cash listing mechanics only after distribution/corporate-action treatment is admitted; sector labels do not substitute for cashflow support.
- **Rates swaps, bespoke OTC options, private assets, physical commodity inventory, structured products and on-chain strategies:** explicitly deferred until a provider, data model, established calculation engine and validated adapter exist. A generic CSV upload is not a capability contract.

## 13. Cross-asset portfolio and optimization consequences

1. A mixed portfolio needs a single base-currency valuation layer with asynchronous market calendars, dated FX conversion, clear stale marks and executable-session masks. Never forward-fill one market's execution merely because another is open.
2. Cash, collateral, short proceeds, option premium, futures MTM, borrow, funding and coupons are different flows. Shared cash does not imply legal or broker-valid cross-margin. The engine must declare supported account interactions.
3. An option chain, future roll or dynamic universe makes requirements conditional. Plan all candidate instruments/windows from the whole allowed search space once. If exact future selections cannot be known without data, use a bounded staged discovery plan before trials; do not download from inside a trial to change candidate evidence unfairly.
4. Optimization constraints must understand units and validity: integer lots, sum of weights, lower strike < upper strike, hedge ratios, DTE versus holding horizon, account exposure caps, only valid combinations. A pruned trial is not a broker/data failure; inapplicable parameters do not deserve an importance score.
5. A new asset class must earn a capability profile covering **inputs → native acquisition → lifecycle/accounting → analysis → export/replay → optimization → validation**. Passing a standalone engine example is not completion of that journey.
6. Latest third-party docs can describe features beyond our pinned release. Record engine/package/adapter version in every result and verify the exact combination before enabling choices. The general VectorBT API has order/signal/custom-order simulation modes and analysis; our adapter remains narrower. [VectorBT portfolio API](https://vectorbt.dev/api/portfolio/base/)

## 14. Where this appears in the frontend

| Page/view | Asset-aware content |
|---|---|
| Library | Filter by research question, market/profile, status and tags; preserve setup lineage; no display of every instrument assumption on each row |
| Setup / Instruments & strategy | Primary user choices and contextual class-specific controls; automatic inferred profile; exact contracts or dynamic-selection rule; sample mapping preview |
| Setup / Capital & execution | Relevant account, size, costs, lifecycle and fill assumptions only; class-aware defaults whose meaning is visible |
| Data review | Compact ready/needs-input coverage by data type; detailed symbol/date/corporate-action/chain/FX issues on demand; return directly to the responsible setup control |
| Run | Save and start; bounded preparation, resumable progress, stop/resume according to engine job capability; navigation away does not cancel |
| Results | Shared equity, drawdown, benchmark, trades, exposure; contextual tabs for distributions/borrow/options legs/Greeks/futures rolls/funding/bond cashflows/fund contributions |
| Optimization study | Only valid parameters, units and conditional choices; stable data evidence; applicable analysis plots; candidate drill-down opens the same result schema |
| Compare & validate | Apples-to-apples period/currency/cost/data basis; visibly mark differing assumptions; test candidate on reserved evidence without retuning on that evidence |
| Saved setup / version history | Freeze or branch profile, rules, data/metadata versions, results, study identity and selected candidate; label modeled assumptions separately from sourced facts |

## 15. Small acceptance examples that prevent large product mistakes

- Date-only cash signals with daily rules use native daily prices, including when stops are present; a minute execution rule makes minute coverage required.
- An optimizer allowed to move from one-day holds to two-hour holds plans sufficient intraday data before evaluating trials.
- "NIFTY" chosen as a signal source asks for the tradable exposure only when the intent calls for trading it; the index cannot silently become a future.
- An options delta-selection study cannot run from underlying OHLC alone. It identifies the missing chain/Greeks capability without silently inventing premiums.
- US and NSE equity options resolve their own lifecycle profile; exercise style and physical/cash settlement are independent fields.
- A futures roll study shows actual close/open roll transactions and separates continuous signal prices from executable contracts.
- A short portfolio with missing borrow evidence names its assumption or remains blocked; long-only engine support is not quietly widened.
- A perpetual study requires funding and applicable mark data for account-level results; a spot candle study does not acquire perpetual semantics merely by adding leverage.
- A bond backtest includes accrued interest and coupons; a fund SIP uses prospective NAV and contribution timing, not equity candle fills.
- Changing any consequential profile setting after a completed run creates a new revision and result while preserving exact replay of the original.
