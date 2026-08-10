# Handoff: Banking Assistant Chat — Mobile-First Redesign

## Overview
Redesign of an existing single-page banking chatbot demo (plain `index.html` + `style.css` + `script.js`).
Scope is the **chat screen only**. Two complete visual directions were designed, each in two breakpoints:

| Id | Direction | Feel |
|----|-----------|------|
| 1a | Calm & trustworthy | Soft neutrals, one deep teal accent, tabular figures, card-based bubbles |
| 1b | Editorial & premium | Warm paper background, Newsreader serif for assistant voice, rules instead of cards |

Both are designed **mobile-first at 390px**, then expanded to a **full-width desktop app shell** (left nav + chat column + right context rail).
Pick one direction before implementing — they are alternatives, not a light/dark pair.

## About the Design Files
The files in this bundle are **design references created in HTML**. They are prototypes showing intended look and behaviour — not production code to copy directly.

The task is to **recreate these designs inside the target codebase's own environment**, using its established patterns. For this project that means the existing vanilla HTML/CSS/JS chatbot demo: keep the current DOM-building and message-rendering logic, replace the markup structure and stylesheet. If the codebase is later moved to a framework (React/Vue), the same structure maps 1:1 onto components (see *Component Breakdown*).

The prototype markup uses inline styles because of the authoring environment. **Do not ship inline styles** — translate them into CSS custom properties + classes (or the codebase's existing convention) as documented under *Design Tokens*.

## Fidelity
**High-fidelity.** Colors, type sizes, spacing, radii, and copy are final. Recreate pixel-accurately at 390px and at desktop widths. Icons are inline 24×24 stroke SVGs (`stroke-width` 1.6–1.8, round caps) — substitute the codebase's icon set if it has one with the same weight.

---

## Screens / Views

### 1. Chat — mobile (390 × 800, the primary target)

**Layout** — single column, three stacked regions, viewport-height flex:

```
[ header        ] fixed height, does not scroll
[ transcript    ] flex: 1, overflow-y: auto
[ composer      ] fixed height, safe-area padding at bottom
```

- Root: `display:flex; flex-direction:column; height:100dvh` (prototype uses a fixed 800px frame; in the app use `100dvh`).
- Transcript padding: **20px 18px 8px**; gap between message groups **14px** (1a) / **20px** (1b).
- Composer padding: **12px 14px 20px** (add `env(safe-area-inset-bottom)`).

**Header**
- 1a: white bar, `1px solid #E7EAE7` bottom border, padding `16px 18px 14px`. 38×38 avatar tile, `border-radius:12px`, `#0E4B45` bg, white 14px/600 initials "AV". Title "Ava" 15px/600, `letter-spacing:-0.01em`. Subline 12px `#6B7A75` with a 6px `#2E8B6F` status dot: "Northbank assistant". Trailing 34×34 menu button, `border-radius:10px`, `1px solid #E7EAE7`, hamburger icon `#6B7A75`.
- 1b: no bar — transparent header on `#F6F1E9`, padding `20px 22px 16px`, eyebrow "NORTHBANK" 11px/`letter-spacing:.16em`/uppercase/`#9C9084` above "Ava" in Newsreader 26px/400. Right side: "Secure" 12px `#7A6E62` + 7px `#8C3A2B` dot. A `1px #E4DBCD` rule inset 22px sits below.

**Transcript contents, in order**
1. Day divider — 1a only: centered 11px uppercase `letter-spacing:.08em` `#97A29C` "Today".
2. Assistant text message — "Morning, Linh. Everything looks steady — no unusual activity since Friday."
   - 1a: bubble `max-width:84%`, white, `1px solid #E7EAE7`, `border-radius:16px 16px 16px 6px`, padding `13px 15px`, 15px/1.5.
   - 1b: no bubble. Timestamp eyebrow "AVA · 08:12" 11px/`.14em`/uppercase/`#9C9084`, then Newsreader 20px/1.45/300.
3. Quick-reply chips row — "Check balance", "Recent spending", "Move money". `display:flex; flex-wrap:wrap; gap:8px`; each `padding:9px 14px` (1a) / `9px 15px` (1b), `border-radius:999px`. 1a: white bg, `1px solid #CBD6D1`, 13.5px/500 `#0E4B45`. 1b: no fill, `1px solid #D6C9B6`, 13.5px `#4A3F35`.
4. User message — "How much is left in my current account?" `align-self:flex-end`, `max-width:80%` (1a) / 82% (1b). 1a: `#0E4B45` bg, white text, `border-radius:16px 16px 6px 16px`. 1b: `#221D18` bg, `#F6F1E9` text, `border-radius:14px`.
5. Assistant reply + **balance card** — "Here it is, updated a minute ago." then the card:
   - 1a: white, `1px solid #E7EAE7`, `border-radius:16px`, `padding:18px`, `gap:14px`. Row 1: label "CURRENT ACCOUNT · 4417" 12.5px/`.06em`/uppercase/`#8A968F` and "+2.1%" 12.5px/500 `#2E8B6F`. Amount **£3,482.10** 34px/600, `letter-spacing:-0.02em`, `font-variant-numeric: tabular-nums`. `1px #EDF0EE` divider. Two label/value rows 13.5px: "Available today / £3,182.10", "Pending out / £300.00" (labels `#6B7A75`, values `#14201C`, tabular).
   - 1b: same content, square corners (no radius), `#FFFDF9` bg, `1px solid #E4DBCD`, `padding:22px`, amount in Newsreader 42px/300, accent `#8C3A2B` for "+2.1%".
6. **Transaction list card** — header "LAST 3 TRANSACTIONS", three rows:
   | Merchant | Meta | Amount |
   |---|---|---|
   | Coffee Union | Today · Card | −£4.20 |
   | Transfer to Savings | Yesterday | −£250.00 |
   | Salary — Meridian Ltd | Fri 26 | +£2,940.00 (positive: `#2E8B6F` in 1a, `#4A6B4E` in 1b) |
   - 1a: each row `padding:11px 14px`, 34×34 `border-radius:10px` initial tile (`#EEF2F0` bg / `#0E4B45` text; credit row `#E6F1EC` / `#2E8B6F`), name 14.5px/500, meta 12.5px `#8A968F`, amount 14.5px tabular. `1px #EDF0EE` separators inset 14px.
   - 1b: no tiles, no card — rows separated by `1px solid #EDE5D8` top borders, amounts in Newsreader 17px.
7. **File attachment (user side)** — 34×34 icon tile + "receipt-hotel.pdf" 14px and "248 KB · dispute this charge?" 12px at 72% opacity. 1a: inside the teal user bubble, tile `rgba(255,255,255,.16)`. 1b: outlined card, `#FFFDF9` bg, `1px #D6C9B6`, tile `#F1E9DC` with `#8C3A2B` icon.
8. **Typing indicator** — three 6px dots (5px in 1b), `animation: blip 1.3s infinite` with `0s / .18s / .36s` delays. 1a: inside an empty assistant bubble. 1b: label "AVA IS WRITING" + dots, no bubble.

```css
@keyframes blip {
  0%, 60%, 100% { opacity: .25; transform: translateY(0); }
  30%           { opacity: 1;   transform: translateY(-3px); }
}
```

**Composer**
- 1a: 44px controls in a row, `gap:10px` — attach (+) button `border-radius:12px`, `1px #E7EAE7`; input fills remaining space, `#F4F5F3` bg, `1px #E7EAE7`, `border-radius:12px`, `padding:0 14px`, 15px text, placeholder `#97A29C` "Ask about your money…"; mic button; send button `#0E4B45` bg, white arrow.
- 1b: borderless — bare + icon, italic Newsreader 18px/300 placeholder `#A79A8C`, bare mic icon, 42px circular `#8C3A2B` send button. `gap:14px`, top rule `1px #E4DBCD`, `#FFFDF9` bg.
- **All tap targets are ≥44px.** Keep that when re-implementing 1b's bare icons (pad the hit area).

### 2. Chat — desktop app shell (≥1024px, prototype frame 1180 × 800)

Three columns: `236px | 1fr | 268px` (1a) / `232px | 1fr | 262px` (1b), full viewport height, each column scrolling independently.

**Left nav** — brand lockup ("Northbank"; 1a adds a 26px `#0E4B45` rounded square, 1b sets the wordmark in Newsreader 21px). Nav items: Assistant (active), Accounts, Payments, Cards, Support — 14.5px, `padding:10px 12px`. Active state: 1a `#EEF2F0` pill with `#0E4B45` text; 1b a 1px bottom rule under the label. Then "RECENT CHATS" with three 13.5px `#6B7A75` entries: "Standing order to Kim", "Card declined in Lisbon", "Overdraft limit". Footer: 30px circular avatar "LT", "Linh Tran" 13.5px / "Personal" 12px, pushed down with `margin-top:auto`.

**Center chat column** — same message system as mobile with wider measures: transcript `padding:28px 32px` (1a) / `32px 40px` (1b); assistant text `max-width:620–640px` at 15.5px (1a) / Newsreader 23px (1b); user bubbles `max-width:520px`. Header adds a right-aligned "Talk to a person" link (`#0E4B45` in 1a, `#8C3A2B` in 1b) and, in 1a, "Secure session · ends 22:40" next to the status dot.
The balance card becomes **two side-by-side cards** — Current 4417 £3,482.10 ("£3,182.10 available · £300 pending") and Savings 9021 £11,050.00 ("+£250 moved yesterday"). 1a: two bordered cards, `gap:12px`. 1b: one block split by a vertical `1px #E4DBCD` rule, top and bottom rules only. The transaction list is dropped at desktop (the right rail carries context instead).

**Right context rail** — "IN THIS CONVERSATION": "Balance shown £3,482.10" and "Attached receipt-hotel.pdf". Then "NEXT STEPS" as three actions — **Open a dispute** (primary), Freeze card 4417, Download statement. Footer note 12px `#97A29C` / `#9C9084`: "Ava can see your accounts but never your full card number. Money movement always asks you to confirm."

**Responsive rules**
- `< 768px`: mobile layout; both side rails hidden (left nav becomes the header hamburger drawer).
- `768–1023px`: center column + left nav; right rail hidden.
- `≥ 1024px`: all three columns.
- `≥ 1440px`: cap the transcript measure at ~720px and center it; do not let bubbles stretch further.

---

## Interactions & Behavior
- **Send** — Enter sends, Shift+Enter newlines; textarea auto-grows to max 5 lines then scrolls. Send button disabled (50% opacity, no pointer) while empty.
- **Optimistic append** — user message appends immediately, transcript scrolls to bottom (`container.scrollTop = container.scrollHeight`; never `scrollIntoView`).
- **Typing indicator** — shown while the reply is pending, removed when the first assistant node renders. Minimum visible time ~400ms so it doesn't flash.
- **Message entry animation** — fade + 6px rise, 180ms `cubic-bezier(.2,.7,.3,1)`. Respect `prefers-reduced-motion` (skip transform, keep opacity, freeze the blip dots).
- **Quick replies** — tapping one sends its label as a user message and removes the chip row. Chips are `<button>`s inside a `role="group"`, horizontally scrollable on mobile if they overflow (no wrap needed if you prefer a single scrolling row).
- **Hover/active** (desktop) — chips: 1a bg → `#EEF2F0`, 1b bg → `#F1E9DC`. Nav rows: bg → `#F4F5F3` / `#F1E9DC`. Send button: 6% darker; `:active` `scale(.97)`. Focus-visible: 2px outline in the accent at 40% alpha, 2px offset.
- **Attachment** — `+` opens a hidden `<input type="file">`; the attachment bubble renders name + size before the reply arrives. Show an inline error row for >10MB or unsupported types.
- **Voice** — mic toggles recording; while active the mic tile turns accent-filled and the placeholder becomes "Listening…". Fall back to hiding the mic if `SpeechRecognition` / `MediaRecorder` is unavailable.
- **Loading / error / empty** — first-load skeleton: header + composer render immediately, transcript shows a single typing indicator. Failed reply: assistant-side row with the accent-red text and a "Retry" text button. Empty conversation shows only the greeting + chip row.
- **Accessibility** — transcript is `role="log" aria-live="polite"`; each message has a visually-hidden "Ava said" / "You said" prefix; amounts get `aria-label` with the currency spelled out; the typing indicator is `aria-label="Ava is typing"`.

## State Management
```
messages: [{ id, role: 'user' | 'assistant', kind: 'text' | 'balance' | 'transactions' | 'file',
             text?, payload?, timestamp }]
draft: string
isSending: boolean          // drives typing indicator + disabled send
quickReplies: string[]      // cleared once used
attachment: { name, size, file } | null
isRecording: boolean
error: string | null
```
Transitions: submit → push user message, clear draft/attachment/quickReplies, `isSending = true` → response → push assistant message(s), `isSending = false`; failure → `error` set, `isSending = false`, keep the draft.
Data: the demo can keep its canned responses. If wired to a real endpoint, `POST /chat { message, attachmentId? }` returning `{ blocks: [...] }` where each block maps to a message `kind` above. Account/transaction figures come from the accounts endpoint, never hardcoded in the view.

## Design Tokens

### 1a — Calm & trustworthy
```css
--bg-app:        #E9E9E6;  /* page behind the shell */
--bg-canvas:     #F4F5F3;  /* transcript background */
--bg-surface:    #FFFFFF;  /* bars, cards, bubbles */
--border:        #E7EAE7;
--border-strong: #CBD6D1;  /* chip outline */
--divider:       #EDF0EE;
--tint:          #EEF2F0;  /* active nav, avatar tiles */
--tint-positive: #E6F1EC;
--accent:        #0E4B45;  /* deep teal: user bubble, send, links */
--positive:      #2E8B6F;
--text:          #14201C;
--text-muted:    #6B7A75;
--text-subtle:   #8A968F;
--text-faint:    #97A29C;
```
Type: **Instrument Sans** 400/500/600. Body 15px/1.5 (15.5px desktop), meta 12.5–13.5px, eyebrow 11–12.5px uppercase `letter-spacing:.06–.08em`, amount 34px/600 `-0.02em` (32px desktop). All figures `font-variant-numeric: tabular-nums`.

### 1b — Editorial & premium
```css
--bg-app:        #E9E9E6;
--bg-canvas:     #F6F1E9;  /* warm paper */
--bg-surface:    #FFFDF9;
--border:        #E4DBCD;
--border-strong: #D6C9B6;
--divider:       #EDE5D8;
--tint:          #F1E9DC;
--accent:        #8C3A2B;  /* clay red: send, links, dots */
--positive:      #4A6B4E;
--text:          #221D18;  /* also the user bubble fill */
--text-muted:    #7A6E62;
--text-body:     #4A3F35;
--text-faint:    #9C9084;
--text-placeholder: #A79A8C;
```
Type: **Newsreader** 300/400 (+300 italic) for assistant voice, amounts, headings; **Work Sans** 300/400/500 for UI, labels, user messages. Assistant text 20px/1.45/300 mobile, 23px/1.42 desktop. Amount 42px/300 mobile, 40px desktop. Eyebrow 11px uppercase `letter-spacing:.14–.16em`.

### Shared
```css
--radius-bubble: 16px;                /* 1a; 14px in 1b */
--radius-tail:   6px;                 /* the flattened corner */
--radius-card:   16px;                /* 1b cards are square */
--radius-control:12px;  --radius-pill: 999px;
--space: 2 4 6 8 10 12 14 16 18 20 22 24 26 28 32 40  /* px steps in use */
--control-size: 44px;                 /* min tap target */
--shadow-frame: 0 24px 60px -24px rgba(20,32,28,.28), 0 0 0 1px rgba(20,32,28,.07);
```
`--shadow-frame` only frames the prototype device mocks — the real app is full-bleed and needs no shadow. Elevation elsewhere is carried by 1px borders, not shadows, in both directions.

## Component Breakdown
`AppShell` → `SideNav`, `ChatPanel`, `ContextRail`
`ChatPanel` → `ChatHeader`, `Transcript`, `Composer`
`Transcript` → `DayDivider`, `MessageGroup`, `TypingIndicator`
`MessageGroup` → `TextBubble`, `QuickReplies`, `BalanceCard`, `TransactionList`, `AttachmentBubble`
`Composer` → `AttachButton`, `MessageInput`, `MicButton`, `SendButton`

## Assets
No bitmap images. All icons are inline stroke SVGs authored in the prototype (plus, mic, arrow-right, hamburger, document) — copy them from the HTML or swap for the codebase's icon library at the same optical weight. Avatars and merchant marks are **initials on a tinted tile**, no imagery. Fonts load from Google Fonts:
```
Instrument Sans:wght@400;500;600
Newsreader:ital,opsz,wght@0,6..72,300;0,6..72,400;0,6..72,500;1,6..72,300
Work Sans:wght@300;400;500
```
Self-host these if the app must work offline.

## Files
- `Banking Chat.dc.html` — the design reference. Contains both directions, each with the mobile (390px) and desktop (1180px) frame, in one canvas. Open it in a browser to inspect exact values; ids `1a` / `1b` mark the two directions.
- Original demo (`index.html`, `style.css`, `script.js`) is the implementation target — keep its message-rendering logic, replace markup + styles.

## Open questions for the team
1. Which direction ships — 1a or 1b?
2. Is the right context rail in scope for v1, or defer it and ship the two-column shell?
3. Are real account and transaction endpoints available, or should the demo keep canned data?
