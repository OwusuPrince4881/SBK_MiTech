# MineMat AI project guidance

## Purpose

MineMat AI is intended to support preliminary assessment of soil and aggregate
materials used in mine construction. It is decision support, not a replacement
for qualified engineering judgment.

## Architecture

- `apps/web` is the planned React and TypeScript frontend.
- `apps/api` is the planned Python FastAPI backend. Keep HTTP handling,
  engineering logic, domain concepts, persistence, and ML integration in their
  respective modules.
- `packages` is reserved for shared contracts or utilities when a real need
  arises.
- `docs` holds project and engineering documentation; `tests` holds
  cross-component tests.
- PostgreSQL is the planned structured-data store. Uploaded photographs should
  be stored separately, with references and metadata held in the database.
- Future ML inference should be accessed through the backend. Keep the initial
  design simple and avoid introducing separate services before they are needed.

## Coding standards

- Prefer small, clear, maintainable changes and follow existing project
  conventions.
- Keep API validation and transport concerns separate from domain and
  engineering logic.
- Validate inputs, handle errors explicitly, and test behavior at the
  appropriate level.
- Keep credentials, environment-specific configuration, generated files, and
  local datasets out of version control.
- Do not add dependencies or services without a concrete requirement.

## Engineering limitations

- A photograph alone cannot establish material quality, compliance, or
  engineering suitability. Visual analysis may provide observations only.
- Do not infer suitability when required laboratory results or applicable
  engineering criteria are missing.
- Any future assessment must combine relevant laboratory results with
  appropriate, validated criteria and communicate its evidence, uncertainty,
  and limitations.
- Do not invent engineering thresholds, claim regulatory compliance without
  validation, or present model output as a professional engineering decision.
- Do not create or present fabricated laboratory results as real data.
