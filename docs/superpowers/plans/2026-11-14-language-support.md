# Turkish / English Implementation Plan

> **For agentic workers:** Use the approved design and TDD; work only in your assigned files. Parent integrates and reviews before publication.

**Goal:** Make UI and new responses selectable in Turkish and English.
**Architecture:** Explicit React catalogs plus request-local backend localization. HTTP language interface is shared; file ownership is disjoint.
**Tech Stack:** Existing React/TypeScript/Vite and FastAPI/Python, no translation service.
**Spec:** docs/superpowers/specs/2026-11-14-language-support-design.md

## Global Constraints
- Locales `tr` and `en`, default `tr`, persisted preference.
- Send `Accept-Language` on API requests, `language` in chat request.
- Never translate G-code/IDs/numbers/user text/history; do not alter CAD/CAM calculations.
- Preserve secrets exclusions, no real API calls, no agent commits/pushes.

## Review Focus
- Stored preference unavailable/invalid: safe Turkish fallback.
- Concurrent requests in different locales: no cross-request language leak.
- Dynamic error/report text and dialogs must translate, not just navigation.
- Switching with existing messages/model must not rewrite or reset them.
- Missing catalog entries and interpolation must not produce undefined text.

## Task 1: Web UI localization
Files: frontend/src/i18n*, frontend/src/App.tsx, frontend/src/api.ts, all frontend components; frontend tests/config.
Interface: export locale state/hook plus translate function; API attaches Accept-Language and chat language.
- [ ] Write/run failing tests for fallback, interpolation, persistence and representative translated views.
- [ ] Implement catalog/provider/selector and migrate visible UI strings across all active components, including title/aria/placeholder, settings, inspector, workshop and audit screens.
- [ ] Run tests and production build, audit uncovered strings.

## Task 2: Backend response localization
Files: backend/app/i18n*, backend/app/main.py, backend/app/llm/*, presentation fields in validation/profile tools; backend/tests/test_i18n.py.
Interface: Accept-Language request locale (`en`/`tr`), optional ChatReq.language overrides for new chat, default Turkish for callers without locale.
- [ ] Write/run failing tests for localized API findings/errors, mocked provider language instructions, offline replies, data/history invariants and request isolation.
- [ ] Implement translations without changing algorithms or persisted user content.
- [ ] Run complete backend suite with project interpreter and clean PYTHONPATH.

## Task 3: MCP viewer and integration
Files: mcp_ui/src/*, backend/app/mcp_ui/index.html, README.md.
- [ ] Add viewer locale catalog and selection, respecting stable protocol IDs.
- [ ] Build bundle and verify both languages in actual browser.
- [ ] Run full suites, inspect diff with independent reviewer, correct important findings, scan secrets, commit/push and compare remote SHA.
