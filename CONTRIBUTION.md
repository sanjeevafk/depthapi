# Contributing to KnowBear

Thank you for considering contributing to **KnowBear**!  
This is a solo/student-maintained open-source project under the Apache 2.0 license. Contributions of any size are welcome — from tiny bug fixes and documentation improvements to new explanation styles, better model routing, UI polish, or performance ideas.

Even if you're just starting with open source, this is a friendly place to learn and experiment.

## Ways to Contribute

- Report bugs or unexpected behavior  
- Suggest new explanation styles (e.g., "explain like I'm a high-school physics teacher", "in the style of xkcd")  
- Improve the judge/ensemble logic or add new models  
- Enhance frontend UX/animations/accessibility  
- Add or expand tests (pytest for backend, vitest for frontend)  
- Fix typos, improve docs/README, or clarify code comments  
- Share performance optimizations (caching, inference speed)  
- Propose architectural improvements (especially during the v2 refactor)

## Getting Started

1. **Fork & clone** the repository  
   ```bash
   git clone https://github.com/YOUR-USERNAME/knowbear.git
   cd knowbear
   ```

2. **Set up the development environment** (see README Quick Start section)  
   - Backend: `cd api && pip install -r requirements.txt` + create `.env`  
   - Frontend: `cd src && pnpm install && pnpm dev`  
   - Run both in parallel and test your changes locally.

3. **Create a branch** for your work  
   Use a descriptive name (Conventional Commits style is appreciated but not required):
   ```bash
   git checkout -b fix/usage-gate-refresh
   # or
   git checkout -b feat/add-xkcd-style-explanation
   ```

4. **Make your changes**  
   - Keep commits small and focused  
   - Write clear commit messages (e.g. `fix: resolve abort controller leak on query cancel`)  
   - If adding a new feature, include a short description in the PR

5. **Test locally**  
   - Run backend tests: `cd api && pytest`  
   - Run frontend tests: `cd src && pnpm test` (when coverage grows)  
   - Manually verify the UI and API responses

6. **Push & open a Pull Request**  
   - Push your branch: `git push origin your-branch-name`  
   - Open a PR against `main`  
   - Fill in the PR template (below) with:  
     - What problem it solves  
     - How you tested it  
     - Any screenshots / before-after (very helpful for UI changes)

## Pull Request Guidelines

Use this simple checklist in your PR description:

```markdown
**What does this PR do?**

**Why is it needed?** (link to issue if exists)

**How was it tested?**
- [ ] Unit tests added/updated
- [ ] Manually verified in browser
- [ ] No regressions in core flows (query → layered output → export)

**Screenshots** (UI/UX changes only):

**Additional notes / trade-offs:**
```

We use squash-merge for most PRs to keep history clean, but feel free to request otherwise.

## Code Style & Conventions

- **Backend**: Follow PEP 8 + use ruff/black (pre-commit hooks coming soon)  
- **Frontend**: Use Prettier + ESLint (already configured via Vite)  
- **Commit messages**: Conventional Commits preferred (`fix:`, `feat:`, `refactor:`, `docs:`, `chore:`)  
- **New dependencies**: Only add if truly necessary — discuss in the PR first

## Licensing

By contributing, you agree that your contributions will be licensed under the project's **Apache 2.0 License** (same as the rest of the codebase). No separate CLA is required.

## Questions or Ideas?

- Open an **issue** first for larger features or architectural changes  
- Feel free to ping me (@voidcommit-afk) in the issue/PR  

Thank you again. Every contribution helps make KnowBear better!

