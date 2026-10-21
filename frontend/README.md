# Agent Talk web client

React + TypeScript + Vite, with Tailwind and shadcn/ui components (Base UI primitives).
The Python package serves the built client from `src/agent_talk/web/`; users never need Node.

```sh
npm ci
npm run dev      # Vite on :5173, proxying /api to agent-talk serve on :7710
npm run build    # type-check and build into ../src/agent_talk/web
npm test         # Playwright: desktop and phone, against the server with a fake Codex
```

`npm test` needs a built client and a Chromium. Point `PLAYWRIGHT_CHROMIUM_EXECUTABLE`
at an existing Chromium, or run `npx playwright install chromium`.
