# ATLAS — Evidence-Grounded Enterprise Search Platform

Security- and provenance-aware enterprise search.

ATLAS is a research-grade portfolio project designed to model a realistic
enterprise search system. It is being built incrementally through tightly
scoped engineering milestones.

## Current Phase

**Phase 1C — Milestone 1**: NovaStack organizational entity generation
(Users, Teams, Customers, Services).

## Quick Start

```bash
# Install test dependencies
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -v

# Generate the NovaStack dataset
python scripts/generate.py
```

Generated data is written to `data/raw/novastack/`.

## Project Structure

```
ATLAS/
├── data/raw/novastack/       # Generated organizational data (JSON)
├── src/novastack/            # Generator package
│   ├── config.py             # Seed, tenants, configurable distribution
│   ├── models.py             # Typed dataclass models
│   └── generator.py          # Deterministic entity generator
├── scripts/generate.py       # CLI entry point
├── tests/                    # Test suite
├── docs/                     # Project documentation
└── reports/                  # Analysis reports (future)
```

## Documentation

- [Project Context](docs/PROJECT_CONTEXT.md)
- [Current Milestone](docs/CURRENT_MILESTONE.md)
- [Decisions](docs/DECISIONS.md)
