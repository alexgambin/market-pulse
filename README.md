# Market Pulse

An AI research agent for macro markets that makes explicit, checkable forecasts and keeps score of them.

Several times a day Market Pulse pulls news from ~20 RSS feeds, triages it with Claude Haiku, and hands the relevant items to a Claude Sonnet agent. The agent links each story to persistent narrative threads stored in SQLite, writes a synthesis, and records probabilistic predictions as machine-checkable contracts (instrument, condition, target level, deadline). A deterministic validation engine resolves those contracts against daily price data and scores them with the Brier score, so the system's forecasting skill is measured rather than asserted.

Around that core:

- **Macro and positioning data:** FRED, EIA, CFTC Commitments of Traders, crypto derivatives and on-chain metrics, plus an economic calendar with pre- and post-event analysis.
- **Polymarket simulation:** a paper-trading engine with virtual USD that turns the agent's views into simulated maker positions.
- **Doctrine and library:** the agent reasons with distilled frameworks (market microstructure, money management, open-economy macro) and can query a full-text library of reference books.
- **Cost observability:** every model call is logged with tokens and cost per component and per run.

## Results

As of 10 September 2026:

| Metric | Value |
|---|---|
| News items ingested | 9,504 |
| Predictions recorded | 110 |
| Predictions resolved | 97 |
| Mean Brier score | 0.223 |

Lower Brier is better; always answering 50% scores 0.25.

## Stack

- Python 3.12, FastAPI, Uvicorn, APScheduler
- SQLite, including FTS5 for the book library
- Anthropic API: Claude Haiku 4.5 (triage), Claude Sonnet 4.6 (agent)
- feedparser, yfinance, FRED, EIA, CFTC (Socrata), Polymarket Gamma API
- Vanilla HTML/CSS/JS frontend
- Node.js for the original paper-trading engine (`paper-engine.js`)

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # add at least ANTHROPIC_API_KEY
set -a && source .env && set +a

python fuentes.py               # optional: check which RSS feeds respond
uvicorn main:app --port 8000
```

Open http://localhost:8000 and press **Ejecutar pasada** (run a pass). The SQLite database is created on first start. Scheduled runs fire at 06:45, 10:00, 14:00, 18:00 and 21:45 Europe/Madrid while the server is up.

Tests run offline:

```bash
python -m unittest discover -s tests -q
```

The book library needs `pip install pymupdf` (plus `pytesseract` and Tesseract for scanned books); no books are included in this repository.

## Documentation

The UI, code comments and design docs are in Spanish:

- [VALIDATION.md](VALIDATION.md): deterministic prediction validation
- [OBSERVABILIDAD.md](OBSERVABILIDAD.md): AI cost observability
- [POLYMARKET.md](POLYMARKET.md): Polymarket simulation
- [POSITIONING.md](POSITIONING.md): COT / positioning view

## Built with Claude Code

Market Pulse was designed and built with [Claude Code](https://claude.com/claude-code).

## License

Copyright (c) 2026 Alex Gambín Salat. All rights reserved. See [LICENSE](LICENSE).
