<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Involo Frontend Rules

- Target Next.js 16 App Router, React 19, strict TypeScript, and Tailwind CSS 4.
- Verify framework behavior in the installed Next.js docs before using unfamiliar
  or version-sensitive APIs.
- Keep server/client boundaries explicit. Add `"use client"` only when hooks,
  browser APIs, or event handlers require it.
- Use `src/lib/api.ts` for backend calls and `src/lib/types.ts` for shared contracts.
  Requests must retain `credentials: "include"`; never read HttpOnly cookies.
- Preserve the API client's single refresh-and-retry behavior. Avoid request loops.
- Enforce authorization in the backend; frontend role checks are UX only.
- Reuse existing components and visual tokens before introducing new patterns.
  Maintain keyboard behavior, semantic HTML, labels, focus, loading, empty, and
  error states.
- Add Testing Library/Vitest coverage for user-visible behavior. Prefer role/label
  queries over implementation selectors.
- Run focused Vitest tests, then lint, typecheck, full tests, and build as risk
  warrants.
