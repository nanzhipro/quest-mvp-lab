# quest-mvp-lab — internal conventions

This file is the **internal** rulebook for quest-mvp-lab (agent-facing). It must NOT be
mirrored into `README.md`, which is written from a public, shareable perspective.

## Layout

- **One directory per MVP/demo.** Each project is fully independent; no cross-project shared code.
- **Best practices (mandatory).** Every MVP follows the best engineering practices of its technical domain: language-idiomatic project structure and tooling (SwiftPM, Cargo, CMake — not hand-rolled scripts), automated tests where the domain supports them, and clean build/run/verify commands.
- **README per project (mandatory).** Follow GitHub best practices: background, key decisions, build/run/verify commands, conclusion. A project without a README is incomplete.
- **Master index sync (mandatory).** Every time a project is added, updated, or removed, the project table in `quest-mvp-lab/README.md` must be updated in the same change.
- **Naming**: `<topic>-mvp` (concept / tech-selection validation) or `<topic>-demo` (example showcase).
- **Bundle IDs**: use the `com.nanzhipro.*` prefix when a bundle ID is needed. Keep demo artifacts unsigned unless distribution requires signing.

## Gitignore layering (mandatory)

Git applies layers from far to near; every new project must follow all applicable layers:

1. **Global layer**: `~/.gitignore_global` (`git config --global core.excludesFile`) — machine-level OS junk.
2. **Shared layer**: `quest-mvp-lab/.gitignore` — macOS/editor junk, `target/`, `.build/`, and the certificate/key/provisioning-profile guard.
3. **Project layer**: each project's own `.gitignore` — project-specific build products (`*.app`, dylibs, demo binaries).

- `Cargo.lock` must be **committed** for binary crates (reproducible builds) — never ignore it.
- If a project is later extracted into its own repo, merge the shared-layer patterns into its own `.gitignore` (a project-level `.gitignore` must be self-sufficient on its own).
- Build caches (`target/`, `.build/`) are cleaned when moving projects.
