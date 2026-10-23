# Agent Talk web client

React + TypeScript + Vite, with Tailwind and shadcn/ui components (Base UI primitives).
The Python package serves the built client from `src/agent_talk/web/`; users never need Node.

```sh
npm ci
npm run dev      # Vite on :5173, proxying /api to agent-talk serve on :7710
npm run build    # type-check and build into ../src/agent_talk/web
npm test         # unit tests, then build, then Playwright on desktop and phone
npm run test:unit  # Vitest only: src/**/*.test.ts
```

Playwright runs against the built app with a fake Codex and fake speech, and Chromium's fake
microphone plays a generated tone, so the voice tests cover capture end to end without models.

`npm test` needs Playwright's Chromium: run `npx playwright install chromium`
here in `frontend/` (from the repo root, npx fetches a different Playwright and its browser).
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` still overrides it.

shadcn/ui's stylesheet is copied into `src/shadcn.css` (MIT, see NOTICES) rather than
installed: the `shadcn` package is its CLI, whose dependencies only ever run when adding
components. Add one with `npx shadcn@latest add <component>`; if the CLI updates its
stylesheet, copy the new `dist/tailwind.css` over `src/shadcn.css`.
