# Native OpenAlgo research development

Read `docs/research/BRIEF.md` for the product direction and `docs/research/STATUS.md` for the current milestone. Read `CLAUDE.md` and the relevant entries in `docs/INDEX.md` for OpenAlgo's runtime, database and UI conventions. User instructions take precedence over local guidance within host permissions.

Build this as a native OpenAlgo feature: React/TypeScript screens, Flask/Flask-RESTX endpoints, SQLAlchemy metadata, Historify-backed market data and a bounded Python calculation worker. The previous Backtest Engine is a read-only reference and optional source of tested algorithms; do not embed its Streamlit UI or retain its FastAPI server.

Complete authorized reversible work through implementation, appropriate tests and a reviewable result. A plan, file count or skill does not add an approval gate. Ask only for consequential missing information or authorization, and continue independent work first. Use up to two sub-agents for useful independent tasks with separate write ownership. Give concise progress updates and keep checkpoints under ignored `.agent-native/`.

Preserve stated financial semantics and exact saved evidence. Treat the existing evaluator and regression cases as evidence, not a requirement to reproduce an identified defect. Explain and test any intentional numerical difference.

Keep runtime databases, research artifacts, credentials and private references out of Git. Development must use isolated data and service ports. Do not copy the user's broker credentials or live databases, run their existing scheduled strategies, place orders, toggle live/sandbox mode, publish, or deploy as part of this feature build. User authorization for those actions can be provided separately. Follow OpenAlgo's applicable `fd-audit` skill for changed resource-owning code.
