# Contributing

Thanks for contributing to this project. This guide covers local setup, testing, and PR expectations.

**Prerequisites**
- Python 3.11
- Node.js and npm (for workflow linting and release tooling)
- Docker (for local builds and container tests)
- Access to the required secrets for deployment workflows

**Repository Layout**
- `app.py`, `parsing.py`, `rules_engine.py`: service logic
- `fuzzy_concept_resolver.py`: concept matching
- `tests/`: test suite
- `.github/workflows/`: CI and deployment workflows

**Local Setup**
1. Create a virtual environment.
2. Install Python dependencies.
3. Copy `.env.example` to `.env` and update values.
4. Install Node dependencies if you plan to run workflow linting.

**Common Commands**
1. Run the API locally with your preferred ASGI server.
2. Run Python tests with `pytest`.
3. Run workflow linting with `npm run lint:workflows`.

**Testing Expectations**
- Add tests for new behavior and bug fixes.
- Prefer targeted unit tests when possible.
- Keep regression tests small and focused.

**PR Title Format**
PR titles are validated in CI. Use one of these formats:
- `feat(DP-1234): Your title`
- `fix!(DP-5678): Breaking change`
- `RELEASE: vX.Y.Z`

**Branching**
- Feature work should branch from `dev`.
- Releases and deployments are handled by workflows on `main` and `dev`.

**Documentation**
- Update `README.md` or inline docs when behavior changes.
- Document new environment variables in `.env.example`.

**Security And Data**
- Do not include PHI or sensitive data in commits.
- Treat secrets in workflows and logs as sensitive.

**Code Style**
- Keep functions small and readable.
- Avoid large diffs that mix refactors with behavior changes.
