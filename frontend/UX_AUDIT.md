# UX/UI Audit — Clinical Decision Support RAG Platform

Conducted by live-navigating the running app (backend on :8000, frontend
dev server on :5173) as three real accounts: a `user` role, a freshly
registered account promoted to `admin` for privileged views, and by
reading component source for anything the live pass couldn't reach
(responsive breakpoints — the automation environment could not resize
the actual rendered viewport, so those notes are inferred from the
Tailwind classes already in each component and should be re-verified
with real browser dev tools during implementation).

Findings are written before any code is touched, per the brief. Each
finding notes the page it's on and which audit dimension it falls
under, so Phase 3 can trace every fix back to something observed here.

---

## Global / cross-cutting

**Clinical domain feel — reads as a generic tech app, not a clinical one.**
The entire UI runs on a single vivid purple (`oky-purple`) against pure
white, including a full-bleed purple gradient sidebar. It's clean and
usable, but nothing about the palette signals "healthcare" — it would
look identical as a generic SaaS dashboard. This is the single biggest
lever for Phase 2's design-system work: a deeper, quieter primary
(teal/slate) and an off-white surface would change the whole read of
the product with fairly contained effort.

**Navigation sidebar — icon-only, no labels, no grouping.**
The left rail (`Layout.tsx`) is ~56px wide, purple gradient, and shows
seven icons with zero text: chat, documents, FAQ, analytics, agent,
widget-demo, status. Nothing distinguishes "Patient Tools" from
"Administration" — they're one undifferentiated icon stack. The active
route gets a lighter rounded-square background behind its icon, which
works but is subtle against an already-light purple gradient. A
first-time visitor cannot tell what most of these icons do without
clicking through. No tooltips observed on hover.

**Top bar is inconsistent between pages.** Some pages (Chat) have no
visible header bar at all above the content; others (Documents, FAQ,
Analytics, Agent, Status) show a page title + subtitle + the user avatar
menu. The avatar dropdown itself (name, email, "Usage & status", "Sign
out") is fine once opened, but the trigger is a small unlabeled chevron
next to a 2-letter avatar circle — functional, not discoverable.

**Loading states are inconsistent.** Documents shows plain text
("Loading documents…"); Status shows nothing at all for ~3 seconds
(blank content area, no spinner); the Agent dashboard shows a proper
full-panel skeleton overlay with "Loading dashboard…" (this is the
Issue 10 fix and it works well — worth using as the pattern to extend
everywhere else, not building a new one).

**Focus rings** — not checked live (couldn't reliably tab through a
shadow-DOM widget or verify via automation), but grep of the component
CSS is needed in Phase 4; nothing observed on primary buttons suggests
a custom focus style already exists beyond browser default.

---

## Authentication pages (`/login`, `/register`)

Already close to the brief: single centered white card, purple "CR"
logo mark, labels sit above each field (not placeholder-only — this is
already correct, don't regress it), full-width submit button. Genuinely
calm and uncluttered.

Gaps:
- No visible per-field error state was triggerable in this pass (would
  need a deliberately wrong submission to observe placement) — verify
  in Phase 3 that validation errors render under the specific field,
  not as a page-level banner.
- Register's password field says "Password (min 8 chars)" as a label
  suffix rather than inline help text — minor, but worth a consistent
  treatment with any other field hints.

---

## Chat page (`/chat`) — highest-traffic surface

**Agent step indicator is a full-width, animated intrusion, not a
subtle sidebar.** While a query streams, the UI shows one giant glowing
pulsing orb (~90px) with a label like "Dense Retriever…" centered in
the message column. This is the opposite of the brief's ask (see
"Agent step indicators… should feel like a subtle process sidebar, not
a full-width intrusion"). Once the response *completes*, the same data
renders as a compact "AGENT PIPELINE" row of small green-check pill
chips (Emergency Detector, Query Analyzer, Dense Retriever, Sparse
Retriever, Fusion Agent, Reranker Agent, Generator) — that resting
state is actually good and close to spec. The fix is specifically the
in-progress visualization, not the completed one.

**Citations are a slide-in side panel, not inline chips.** Clicking
"8 sources" opens a right-hand "Sources (N)" drawer listing `[1]
filename…` with a text snippet — useful, but the filename is truncated
mid-word with no page number or authority badge anywhere in the list,
so two chunks from the same document are indistinguishable. The brief
asks for compact chips *below the answer* with doc name + page number
+ authority badge, expandable on click; today the info a chip would
need (page number, authority) isn't in the panel at all.

**Faithfulness badge already uses semantic color and a text label**
("Faithfulness 90% · PASS" in green) — good baseline, keep the
mechanism, just confirm WARN/FAIL map to amber/red (couldn't trigger
those states live) and add the hover tooltip explaining the score,
which isn't present today.

**Emergency warning** — not triggered in this pass (would need a query
that matches emergency-term detection); verify in code that it renders
as a full-width red banner per spec rather than a small badge.

**Handoff states** — not exercised live in this pass beyond confirming
the QUEUED banner exists from the earlier backend hardening screenshots
in this same session; re-verify the input-border color change and
green live-dot on HUMAN_ACTIVE match the new agent-accent token once
Phase 2 defines it.

**Input area** has a functional send button and a stop button while
streaming (seen as a red square during generation) — no keyboard-shortcut
hint text, no character count, matches the audit's expectation of gaps
here.

**Empty state** (no conversation selected) is centered, friendly, with
four suggestion chips — good as-is.

---

## Documents page (`/documents`)

- **No drag-and-drop zone.** "Upload PDF" is a plain button that
  triggers the native OS file picker directly — no dropzone, no
  hover/drag visual feedback, no in-page progress indicator for the
  upload itself.
- **Filenames are raw and ugly**: e.g.
  `depression-in-adults-treatment-and-management-pdf-66143832307909.pdf`
  — the generated suffix makes every row hard to scan.
  `pymupdf` (the parser name) is shown as a small caption under each
  filename, which is implementation detail rather than useful
  information to a clinician managing documents.
- **Authority level** is a plain "L5"/"L3" text pill with no visual
  weight differentiation (a "star or number indicator" per the brief
  isn't there — it's just another pill, same color regardless of
  level).
- **Upload date** shows a raw `25/08/2026`, no relative format, no
  hover-for-exact-date.
- **Delete** is a bare trash icon per row with no visible confirmation
  step in the UI (not deliberately tested to avoid destroying real
  library data, but no modal/confirm affordance is visible on hover).

---

## FAQ page (`/faq`)

Copy says "Frequently asked clinical questions, **clustered by
meaning**", but the UI shows a flat list of individual cards — there is
no visual grouping/header structure reflecting clusters, so the
sub-copy currently overpromises what's on screen. Each card shows the
question, an "asked Nx" pill, an optional category tag, and "last Nd
ago" — clean, minimal. No visible hover affordance (arrow/chat icon) to
signal "click to pre-fill chat."

---

## Analytics page (`/analytics`)

**Overview tab**: three stat tiles (Faithfulness Avg, Emergency Flags,
Top Drugs Tracked) render in plain dark text with no color coding at
all — a 75% faithfulness average and a 0-flags count look identical in
weight/color to what a 30% average or a 12-flags count would. Two bar
lists (Query types, Document type usage) have no axis labels, no
legend, no chart title beyond the card heading — plain horizontal
progress bars with a number.

**Live Queue tab — real bug found, not just styling.** As a plain
`user` role, clicking this tab silently force-logs-out the whole app
and redirects to `/login` (the endpoint requires agent/admin and
something in the error path is treating that 401/403 as session
expiry). As `admin`/`agent` it works and shows real operational value:
Queue Length, Active Human Chats, Avg Wait (raw seconds — `21263`,
not "5h 54m"), Handoff Pressure %; a "Waiting patients" list showing
raw seconds and an internal enum value (`agent_disconnected`) directly
to the admin with no friendly label; a "Live with agent" list with a
working "Force resolve" button (Issue 26); an "Online agents" list that
falls back to a raw UUID slice (`2b25eae7`) when an agent has no
`full_name` set.

**Users tab — a real broken-rendering bug.** Every row in the admin
Users list is visually **just a bare role `<select>` dropdown** — no
name, no email, nothing else renders on the row at all. Confirmed via
the accessibility tree that the name/email text nodes exist in the DOM
(`"UX Audit Admin"`, `"ux-audit-admin@test.local"`, etc.) — they're
just not visually rendering, almost certainly a collapsed-flex-sibling
layout bug (the identity column has zero effective width next to the
select). This makes the promote-a-user feature unusable today: an admin
can't tell which row is which user.

No "live" pulsing badge on the Live Queue tab label to signal
real-time data, per the brief's ask.

---

## Agent dashboard (`/agent`)

Loads behind a clean full-panel skeleton ("Loading dashboard…") — this
is the best loading-state implementation in the app and should be the
template, not rebuilt.

Once loaded (verified with a real routed conversation):
- **Capacity bar is a 2px hairline** under "1 / 5 active chats" — easy
  to miss entirely; the brief calls for it to be prominent since an
  agent at capacity needs to know immediately.
- **Queue wait time is raw seconds** again (`wait 42476s · #1`) — same
  gap as Live Queue.
- **Patient context panel dumps raw JSON** (`clinical_context`) in a
  `<pre>` block — functional for a developer, not for a clinical agent
  scanning for relevant history.
- **The chat timeline gets very noisy** on a conversation with repeated
  reconnects: a real thread showed eight consecutive "CONNECTED WITH …"
  / "SPECIALIST DISCONNECTED … RETURNED TO QUEUE" divider lines in a
  row before any real message. Even with the backend reconnect fixes
  from this sprint in place, the event log itself has no collapsing/
  summarizing for repeated connect-disconnect churn, which will keep
  looking alarming to an agent regardless of backend stability.
- Message bubbles already distinguish "PATIENT" (light lavender, left)
  from "YOU" (solid purple, right) with a small caption label — good
  baseline, just needs the agent-accent token applied consistently once
  Phase 2 defines it.
- Tab strip shows patient label + "Slot N of 5" — no unread/activity
  dot per tab as the brief asks for.

---

## Status page (`/status`)

Clean structure already: a green "Ok / All systems operational" banner
with a check-circle icon, an "Infrastructure" list (Redis, Pinecone)
each with an "ok" + green check, a "Models" list, and a personal token
usage panel. Two gaps:
- No response-time/latency numbers shown at all — the health check
  exists, but nothing renders duration, so the "color by acceptable
  range" ask in the brief has no data to key off yet without a backend
  addition (flagged for awareness; this may be a Phase-3-appropriate
  purely presentational read of data already available, or may need
  a small additive field — decide when implementing, and if the latter,
  document it explicitly like the rules require for structural
  changes).
- No visible loading skeleton — the page is blank for ~2–3 seconds
  before content pops in all at once.

---

## Widget demo (`/widget-demo`) and the widget itself

The fake clinic page ("Riverside Family Clinic") is simple but
plausible — header nav, hero copy, hours line — reads fine as an embed
context, doesn't urgently need more visual richness.

- **Embed code block has no syntax highlighting and no copy button** —
  it's a plain monospace white-on-black box. The brief explicitly asks
  for both.
- The widget bubble itself uses the clinic's configured brand color
  (teal in the demo), correctly distinct from the app's own purple —
  that's intentional per the config, not a bug.
- Live widget interaction (typing into its input) could not be
  exercised reliably through browser automation in this pass — the
  widget renders in an isolated context the automation's DOM tools
  couldn't address by reference, only by raw pixel coordinates, and
  those clicks weren't landing on the actual input. Its message-bubble
  styling should be checked manually (or via a quick local script) at
  the start of Phase 3's widget step rather than assumed identical to
  the main app's `ChatMessage` component — the two are separate
  implementations (`widget/src/widget.ts` renders via manual DOM
  construction, not React).

---

## Accessibility — preliminary, full pass is Phase 4

Not exhaustively tested yet (that's Phase 4), but two things already
visible from this pass worth flagging early:
- Sidebar nav icons have no visible text label or (unverified) aria-label
  — needs confirming in `Layout.tsx`.
- Color is the *only* signal on several stat tiles (Faithfulness Avg,
  Emergency Flags counts) — no icon or text-based severity cue
  alongside the color, which the brief explicitly calls out as a
  colorblind-accessibility requirement.

---

## Summary — where the effort is best spent

Ranked by (user impact × how concrete the fix is):

1. **Users tab rendering bug** (Analytics) — currently broken, not just
   unpolished; blocks the whole promote-a-user flow.
2. **Live Queue force-logout for non-privileged roles** — a real bug
   masquerading as a permissions boundary; worth a minimal, documented
   fix alongside the styling pass since it directly affects the page
   the styling work touches.
3. **Agent step indicator during streaming** — the single most visible,
   most-seen (every chat message) mismatch with the brief.
4. **Human-readable wait times** — appears in three places (Agent
   dashboard queue, Analytics Live Queue waiting list, Analytics avg
   wait stat) with the identical raw-seconds problem; one shared
   formatter fixes all three.
5. **Design tokens (Phase 2)** — underpins the "generic tech app" vs.
   "clinical, trustworthy" gap that no single-component fix can solve
   alone.
6. Everything else in this document, in the implementation order
   already given.

---

## AFTER — what changed, page by page

All 17 implementation steps are complete and verified live (typecheck
clean throughout, no console errors on any page). Summary against the
findings above:

**Global.** Primary rebranded purple → deep teal by retinting existing
design-token *values* in `tailwind.config.cjs` (not renaming classes),
so the entire app — including components never directly touched, like
`DrugInteractionCard`'s left-border accent — picked up the new identity
for free. Added a full token set (semantic success/warning/danger/info
colors, a dedicated `agent` violet reserved only for "a human is here,"
typography/radius/shadow/motion tokens) in `tokens.css`. Added a global
`:focus-visible` ring so every interactive element — not just form
inputs — has a visible, consistent focus indicator.

**Navigation.** Icons now grouped (Patient tools / Clinical data /
Monitoring / Administration) with dividers; active-state changed from a
barely-visible translucent overlay to a solid white pill — genuinely
unmissable now.

**Auth pages.** Labels properly associated via `htmlFor`/`id`; errors
render as a real alert box with `role="alert"`. Fixed a real bug along
the way: a wrong password showed "Your session expired" instead of
"Invalid email or password" (the generic session-expiry copy was wrongly
applied to a page where no session exists yet).

**Chat message components.** Replaced the full-width 88px pulsing orb
that dominated every streaming message with the already-existing compact
`AgentPipeline` pill row — the fix was showing it one phase earlier, not
building something new. Retinted the orb's raw CSS from purple/pink to
teal so it no longer visually claims to be "a human is here." Added a
faithfulness-score tooltip and a count-up animation. Messages now slide
in from their sender's side; pipeline steps fade in one at a time as
they arrive instead of popping in as a static list.

**Chat handoff state UI.** QUEUED now replaces the input area with a
purposeful waiting indicator (position, reassurance copy, cancel) instead
of a banner sitting above a still-typeable input. HUMAN_ACTIVE now uses
the dedicated `agent` violet (banner + input border) instead of generic
green, so "a person is here" reads as its own distinct signal. Found and
documented (not fixed — needs backend work) a real gap: "Cancel" only
updates the UI; the backend queue entry survives until the socket's
disconnect-grace period silently reaps it.

**Chat input.** Added a visible `Enter to send · Shift+Enter for new
line` hint and a character count past 200 characters.

**Documents page.** Corrected a misreading from the initial pass — the
upload flow already had a full modal, not a bare file picker. What it
genuinely lacked: a proper drag-and-drop zone (added, with hover/drag
visual states), an indeterminate progress indicator during upload,
star-based authority-level display (replacing a flat "L3"/"L5" pill),
and relative upload dates ("Yesterday") with the exact date on hover.

**FAQ page.** Added a hover-reveal arrow — the cards were already
single fully-clickable buttons with a hover state, just missing the
explicit affordance icon the brief asked for. (Also corrected an
audit misreading: each card already represents one full paraphrase
cluster, matching the "clustered by meaning" copy — there wasn't a
missing grouping layer to add.)

**Analytics page.** Stat tiles (faithfulness average, emergency flags)
now carry semantic color on the number itself, not just a fixed icon —
a 78% average now visibly reads as "warning," not identical to a 95%.
Live Queue tab got a pulsing "live" dot, and every raw-seconds value
(wait times, chat duration) plus cryptic reason enums
(`agent_disconnected`) now render through shared, human-readable
formatters. Fixed the Users tab: it was rendering as a bare column of
role dropdowns with zero visible name/email — a collapsed-flex-width
bug, not a missing feature — now fully readable.

**Agent dashboard.** Capacity bar thickened from a 1.5px hairline to
something an agent actually notices at a glance, plus a "FULL" label at
capacity. Queue wait times use the same shared formatter as Analytics.
The longest-waiting patient is now visually emphasized. Clinical context
renders as a readable label/value list for the common flat-object case,
falling back to JSON only for nested data — no more raw `{ }` dumps for
the normal case. Handoff reason uses the same friendly labels as
Analytics. Tab switches now fade.

**Status page.** Added a shape-matched loading skeleton for the blank
~2-3s gap before content appeared. (Response-time coloring was left
as a known, explicitly-flagged gap — the data isn't in the API response
today, and adding it would mean a backend change outside this pass's
scope.)

**Widget demo.** Embed code block now has a copy button and a small
line-based highlighter (attribute names/values colored distinctly) —
still no new dependency, just enough to make the snippet scannable.

**Widget itself.** Human-agent bubbles get their own tinted background
and a dedicated violet accent instead of a thin green border easily
read as a success/positive signal. The bot's own pipeline-step chips
moved off that same violet onto neutral slate, so the two signals
(bot thinking vs. human present) can't be confused. Added the
faithfulness badge entirely — the widget was silently dropping the
event the backend already streams, so patients using the embedded
widget never saw it even though `ChatPage` users always have.

**Accessibility pass.** Global focus ring (above). Filled in missing
`aria-label`s on icon-only buttons across Documents, FAQ, Status,
Analytics, Agent dashboard, the shared Modal's close button, and
Chat input's toolbar — several had only a `title` (or, for three of
them — Modal close, Status refresh, Agent dashboard refresh — neither).

**Micro-interactions.** Message slide-in, pipeline-step fade-in,
faithfulness count-up, and agent-dashboard tab fade — all covered above,
listed together here since they're one coherent pass across the same
files.

**Not done, and why.** The Live Queue force-logout bug (a non-agent role
hitting an agent-only endpoint gets fully logged out rather than shown a
permission message) traces to the backend returning 401 instead of 403
for role checks — a real fix needs an API status-code change, which is
business logic and out of scope for a styling-and-structure pass. The
QUEUED "Cancel" button's incomplete server-side effect has the same
shape: fixable, but not from `classNames`/`aria`/layout alone. Both are
flagged here rather than fixed unilaterally.
