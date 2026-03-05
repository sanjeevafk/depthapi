**1. Current Project State**  
**Architecture summary:** KnowBear is a monorepo with a React 18 + Vite frontend (`src/`) and a FastAPI backend (`api/`). The frontend uses Zustand for state, Supabase for auth and data sync, SSE streaming for chat responses, and Tailwind + Framer Motion for UI. The backend exposes `/api/messages` (chat streaming), `/api/query` and `/api/query/stream` (legacy topic flow), `/api/export`, `/api/history`, and `/api/pinned`, with Supabase persistence and optional Redis rate limiting.

**Major implemented features:**  
- Chat experience with conversations, Supabase sync, and SSE streaming (`src/stores/useChatStore.ts`, `src/hooks/useMessages.ts`, `api/routers/messages.py`).  
- Conversation list sidebar for chat (`src/components/chat/ConversationList.tsx`).  
- Mode selection and premium gating (`src/components/chat/ModeToggleBar.tsx`, `src/lib/chatModes.ts`, `src/components/UpgradeModal.tsx`).  
- Legacy topic explain flow with caching and history (`src/store/useKnowBearStore.ts`, `src/lib/responseCache.ts`, `src/components/Sidebar.tsx`, `api/routers/query.py`, `api/routers/history.py`).  
- Export to txt/md (premium-gated on backend) (`src/components/ExportDropdown.tsx`, `api/routers/export.py`).  
- Mermaid rendering (non-lazy) in chat messages (`src/components/Mermaid.tsx`, `src/components/chat/MessageList.tsx`).

**Major missing components (relative to `local-docs`):**  
- Message list render granularity and memoization to prevent O(N) re-renders.  
- SSE schema validation (Zod) and robust parsing guardrails.  
- Chat UI refactor items: header, sidebar search/pins/profile, message action toolbar, regeneration modal and flow.  
- Store unification (legacy + chat) and modern server-state handling (TanStack Query).  
- Robust streaming resilience (per-message abort, exponential backoff) and persistence error recovery.

---

**2. Task Reconciliation**  
(Tasks extracted from `local-docs/architecture_evaluation.md` and `local-docs/refactoring_proposal.md`, deduped and normalized.)

1. **Lazy-load Mermaid and heavy export libs** — **Status: Not Started**.  
Evidence: `src/components/Mermaid.tsx` and `src/components/MermaidDiagram.tsx` import `mermaid` at top level; no `React.lazy` or `await import()` is used. No frontend usage of `pdfmake`/`jspdf` exists, and PDF export is disabled on backend (`api/routers/export.py`). Doc/code mismatch: docs assume heavy export libs are bundled; code does not import them.

2. **Optimize global animations (stars/glow) for performance** — **Status: Partial**.  
Evidence: starfield is already pure CSS (`src/index.css`, used in `src/pages/LandingPage.tsx`), but no `will-change` or explicit perf hints are present; other animations still use Framer Motion.

3. **Message registry pattern (messagesById + messageIds)** — **Status: Not Started**.  
Evidence: `useChatStore` stores `messages: Message[]` only; `MessageList` maps full array each render (`src/stores/useChatStore.ts`, `src/components/chat/MessageList.tsx`).

4. **Memoize Markdown rendering per message** — **Status: Not Started**.  
Evidence: `ReactMarkdown` is rendered directly inside `MessageList` without `React.memo` (`src/components/chat/MessageList.tsx`).

5. **Zod schema validation for SSE payloads** — **Status: Not Started**.  
Evidence: `src/api.ts` and `src/stores/useChatStore.ts` parse JSON with `JSON.parse` and `try/catch`; no `zod` dependency in `package.json`.

6. **Unify legacy store into chat store** — **Status: Not Started**.  
Evidence: `src/store/useKnowBearStore.ts` (legacy) and `src/stores/useChatStore.ts` (chat) both exist; no merge. `ModeContext` exists but is unused (`src/context/ModeContext.tsx`).

7. **Migrate server state to TanStack Query** — **Status: Not Started**.  
Evidence: no React Query dependency in `package.json`; conversations/history are fetched manually in Zustand and hooks (`src/stores/useChatStore.ts`, `src/hooks/useConversations.ts`).

8. **Replace custom response cache with TanStack Query persister** — **Status: Not Started**.  
Evidence: `src/lib/responseCache.ts` still used for legacy flow.

9. **Sidebar with conversations + pinned + search + profile** — **Status: Partial**.  
Evidence: chat sidebar exists (`src/components/chat/ConversationList.tsx`) but has no search, pins, or profile. Legacy sidebar includes profile and history (`src/components/Sidebar.tsx`) but is not used in chat. `PinnedTopics` component exists but is unused (`src/components/PinnedTopics.tsx`).

10. **Chat header with editable title + status pills** — **Status: Not Started**.  
Evidence: `ChatPage` has no header component; conversation titles are not editable in UI (`src/pages/ChatPage.tsx`, `src/components/chat/ConversationList.tsx`).

11. **Assistant message mode badge + action toolbar** — **Status: Partial**.  
Evidence: mode badge exists (`formatModeLabel` in `MessageList`); no per-message toolbar for copy/regenerate/share (`src/components/chat/MessageList.tsx`).

12. **User messages right-aligned** — **Status: Complete**.  
Evidence: `MessageList` uses `justify-end` for user messages (`src/components/chat/MessageList.tsx`).

13. **Input area: floating layout, quick switcher, “Thinking…” skeleton** — **Status: Partial**.  
Evidence: input is fixed at bottom with no floating treatment (`src/components/chat/ChatInput.tsx`); quick switcher exists but as `ModeToggleBar` above input, not next to send (`src/components/chat/ModeToggleBar.tsx`); no skeleton loading in chat (only spinner).

14. **Regeneration modal + flow** — **Status: Not Started**.  
Evidence: no `RegenerationModal` component or regenerate actions in chat; `MobileBottomNav` has `onRegenerate` prop but is unused (`src/components/MobileBottomNav.tsx`).

15. **Chat state architecture with `messageStates` and UI modal state** — **Status: Not Started**.  
Evidence: `useChatStore` lacks `messageStates` map and `ui` modal state.

16. **Per-request AbortController + cancel on regen** — **Status: Not Started**.  
Evidence: `useChatStore.sendMessage` does not create AbortControllers; only legacy store has one (`src/store/useKnowBearStore.ts`).

17. **Concurrent generations support** — **Status: Not Started**.  
Evidence: single `isLoading` flag in `useChatStore` and input lock; no per-message streaming state.

18. **Persistence error recovery (cache + “Retry Sync” pill)** — **Status: Not Started**.  
Evidence: chat error handling removes placeholder and shows error message only (`src/stores/useChatStore.ts`).

19. **SSE reconnect with exponential backoff** — **Status: Partial**.  
Evidence: legacy `queryTopicStream` has retry fallback (`src/api.ts`), but chat SSE in `useChatStore` has no backoff or reconnection.

20. **Accessibility: high‑contrast mode indicators + keyboard‑navigable modals** — **Status: Partial**.  
Evidence: mode UI has visible accents and keyboard navigation in dropdown (`src/components/chat/ModeToggleBar.tsx`); no regen modal exists to validate modal accessibility.

**Doc/Code Mismatches (highlights):**  
- Docs call out heavy export libs in the initial bundle, but frontend does not import them and backend PDF export is disabled.  
- Docs propose a new chat UI sidebar/search/pins and regen modal, but chat currently uses a minimal conversation list with no search/pins and no regen UI.  
- Docs emphasize a split-brain store and recommend unification; both stores are still present and separate.

**Implementation Gap Map (features in docs but missing/partial in code):**  
All tasks with status `Partial` or `Not Started` above are gaps. Key gaps: Message list granularity + memoization, Zod SSE validation, chat header + sidebar enhancements, regeneration flow, abort/backoff handling, and TanStack Query migration.

---

**3. Actionable Task Checklist**

[x] Lazy-load Mermaid and remove heavy export libs from initial bundle  
Description: Move Mermaid to `React.lazy` and ensure any export-heavy libs load only on demand.  
Relevant files/modules: `src/components/Mermaid.tsx`, `src/components/MermaidDiagram.tsx`, `src/components/ExportDropdown.tsx`.  
Suggested implementation steps: (1) Wrap Mermaid component in `React.lazy` and load via `Suspense`; (2) Replace any static export lib imports with `await import()` in handler; (3) Confirm bundle no longer includes heavy libs at startup.

[x] Optimize global animations (stars/glow) for perf  
Description: Add `will-change` hints or convert any remaining static effects to pure CSS.  
Relevant files/modules: `src/index.css`, `src/pages/LandingPage.tsx`.  
Suggested implementation steps: (1) Add `will-change: transform` to animated layers; (2) Audit Framer Motion use for static background effects; (3) Remove or downgrade unnecessary motion where possible.

[ ] Refactor message storage to registry pattern  
Description: Store messages by id and render `MessageItem` per id to avoid O(N) re-rendering.  
Relevant files/modules: `src/stores/useChatStore.ts`, `src/components/chat/MessageList.tsx`.  
Suggested implementation steps: (1) Change store shape to `messagesById` + `messageIds`; (2) Create `MessageItem` that subscribes by id; (3) Update insertion/update logic accordingly.

[ ] Memoize Markdown rendering per message  
Description: Use `React.memo` and a comparison function to avoid re-render unless content or streaming state changes.  
Relevant files/modules: `src/components/chat/MessageList.tsx`.  
Suggested implementation steps: (1) Extract a `MessageContent` component; (2) Wrap with `React.memo` and custom compare; (3) Replace inline `ReactMarkdown` in list.

[ ] Add Zod schema validation for SSE payloads  
Description: Validate streamed JSON chunks and gracefully ignore malformed data.  
Relevant files/modules: `src/api.ts`, `src/stores/useChatStore.ts`, `package.json`.  
Suggested implementation steps: (1) Add `zod` dependency; (2) Define `MessageChunkSchema`; (3) Use `safeParse` before applying updates.

[ ] Unify legacy and chat stores  
Description: Merge `useKnowBearStore` preferences into `useChatStore` and remove redundant state.  
Relevant files/modules: `src/store/useKnowBearStore.ts`, `src/stores/useChatStore.ts`.  
Suggested implementation steps: (1) Identify shared fields (mode, selectedLevel, sidebar state); (2) Port necessary actions; (3) Remove or isolate legacy store if no longer used.

[ ] Migrate server state to TanStack Query  
Description: Use React Query for conversations/history caching and invalidation.  
Relevant files/modules: `src/stores/useChatStore.ts`, `src/hooks/useConversations.ts`, `package.json`.  
Suggested implementation steps: (1) Add `@tanstack/react-query`; (2) Replace `fetchConversations` and history loading with queries; (3) Add query invalidation on mutations.

[ ] Replace responseCache with TanStack Query persister  
Description: Centralize persistence using React Query’s storage persister.  
Relevant files/modules: `src/lib/responseCache.ts`, legacy flow components.  
Suggested implementation steps: (1) Add persister plugin; (2) Remove LZ-string cache usage; (3) Migrate read/write logic to query cache.

[ ] Build full chat sidebar (search, pins, profile)  
Description: Implement the proposed sidebar UX for chat, optionally reusing legacy components.  
Relevant files/modules: `src/components/chat/ConversationList.tsx`, `src/components/SearchBar.tsx`, `src/components/PinnedTopics.tsx`, `src/components/Sidebar.tsx`.  
Suggested implementation steps: (1) Add search input and filter; (2) Add pinned items section; (3) Add profile/settings area.

[ ] Add chat header with editable title + status pills  
Description: Provide a header showing conversation title and current mode.  
Relevant files/modules: `src/pages/ChatPage.tsx`, new `ChatHeader` component.  
Suggested implementation steps: (1) Create header component; (2) Enable inline title editing with persistence; (3) Display mode pills.

[ ] Add per-message action toolbar (copy, regenerate, share)  
Description: On hover, show actions for assistant messages.  
Relevant files/modules: `src/components/chat/MessageList.tsx`.  
Suggested implementation steps: (1) Add `MessageActionToolbar`; (2) Implement copy; (3) Stub regenerate/share actions.

[ ] Implement regeneration modal and flow  
Description: Allow mode-specific regeneration from assistant messages with abort + new stream.  
Relevant files/modules: `src/stores/useChatStore.ts`, new modal component, `src/components/chat/MessageList.tsx`.  
Suggested implementation steps: (1) Add UI state to open modal with target id; (2) Implement regeneration request with abort; (3) Insert placeholder and stream new response.

[ ] Improve input UX (floating bar, quick switcher, skeleton)  
Description: Match proposed layout and better loading feedback.  
Relevant files/modules: `src/components/chat/ChatInput.tsx`, `src/components/chat/ModeToggleBar.tsx`.  
Suggested implementation steps: (1) Update layout styling to floating bar; (2) Add inline mode dropdown near send; (3) Replace input with skeleton when streaming.

[ ] Add robust streaming resilience (abort + backoff)  
Description: Per-message abort controllers and reconnect/backoff on SSE drop.  
Relevant files/modules: `src/stores/useChatStore.ts`, `src/api.ts`.  
Suggested implementation steps: (1) Track AbortControllers per message; (2) Add retry/backoff on stream errors; (3) Surface status in UI.

[ ] Add persistence error recovery UI  
Description: Cache unsaved responses locally and show “Retry Sync” action.  
Relevant files/modules: `src/stores/useChatStore.ts`, new UI component.  
Suggested implementation steps: (1) Cache failed assistant responses in localStorage; (2) Mark messages with retry state; (3) Add retry action to re-sync.

---

**4. Priority Ordering**

**P0 – Required for core functionality**  
- Refactor message storage to registry pattern  
- Memoize Markdown rendering per message  
- Add Zod schema validation for SSE payloads  
- Add robust streaming resilience (abort + backoff)

**P1 – Important improvements**  
- Add per-message action toolbar  
- Implement regeneration modal and flow  
- Add chat header with editable title + status pills  
- Build full chat sidebar (search, pins, profile)  
- Improve input UX (floating bar, quick switcher, skeleton)

**P2 – Nice-to-have / longer-term**  
- Lazy-load Mermaid and remove heavy export libs from initial bundle  
- Optimize global animations (stars/glow) for perf  
- Unify legacy and chat stores  
- Migrate server state to TanStack Query  
- Replace responseCache with TanStack Query persister  
- Add persistence error recovery UI
