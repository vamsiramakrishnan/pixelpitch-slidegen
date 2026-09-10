# Design System Inspired by Slack

> Category: Productivity & SaaS
> Workplace messaging platform. Aubergine personality, four-color brand system, workspace-first UI.

## 1. Visual Theme & Atmosphere

Slack's interface is defined by the tension between its bold aubergine identity and its restrained, productivity-first message surface. The sidebar -- the emotional center of the product -- is drenched in deep aubergine (`#4a154b` default, customizable per workspace), housing channels, DMs, and workspace navigation. The moment you cross the sidebar boundary, the interface shifts to a clean white message pane (`#ffffff`) with warm gray accents, creating a deliberate visual divide between navigation and content.

The four-color brand system -- Aubergine (`#611f69`), Blue (`#36c5f0`), Green (`#2eb67d`), Yellow (`#ecb22e`) -- appears in the octothorpe logo and throughout product moments: online presence dots, notification badges, reaction emoji backgrounds, and the "magic" loading spinner. Red (`#e01e5a`) serves as the fifth brand color for destructive actions and urgent notifications. These colors are never used for large surfaces; they appear as accents, dots, and small UI moments that reward attention.

Typography is system-native with Slack's custom Lato-derived stack: clean, round sans-serif forms that stay legible at the small sizes a dense chat client demands. Message text sits at 15px -- an intentionally odd size that balances density with readability across hours of continuous reading. Channel names, timestamps, and metadata use weight and opacity variation rather than size changes, keeping the vertical rhythm tight.

**Key Characteristics:**
- Aubergine sidebar (`#4a154b`) as the workspace's emotional anchor
- White message surface (`#ffffff`) with warm gray dividers (`#e8e8e8`)
- Four brand accents used sparingly: blue, green, yellow, red -- never as backgrounds
- 15px message body text -- the critical reading size for all-day chat
- Tight vertical spacing: 4px between messages in the same group, 16px between groups
- Rounded avatars (4px radius at 36px), full-round status dots
- Hover-to-reveal action toolbars on messages (emoji, thread, bookmark, more)
- Workspace switcher as a vertical pill rail on the far left

## 2. Color Palette & Roles

### Primary
- **Aubergine** (`#611f69`): Brand primary. Logo mark, marketing headlines, hero gradients.
- **Aubergine Dark** (`#4a154b`): Default sidebar background, workspace identity.
- **Aubergine Deepest** (`#1a0421`): Sidebar header on darker workspace themes.

### Secondary & Accent
- **Slack Blue** (`#36c5f0`): Online status, link text in sidebar, informational badges.
- **Slack Green** (`#2eb67d`): Active/online presence dot, success states, "connected" indicators.
- **Slack Yellow** (`#ecb22e`): Away/idle status dot, starred items, warning states.
- **Slack Red** (`#e01e5a`): Urgent notifications, unread mention badges, destructive actions, DND status.

### Surface & Background
- **White** (`#ffffff`): Message pane, modal surfaces, card backgrounds.
- **Off-White** (`#f8f8f8`): Thread panel background, secondary surface for visual separation.
- **Light Gray** (`#f4ede4`): Warm background tint used in onboarding and marketing pages.
- **Sidebar Active** (`rgba(255,255,255,0.1)`): Hover/active channel highlight on aubergine sidebar.
- **Sidebar Selected** (`rgba(255,255,255,0.18)`): Currently selected channel on sidebar.
- **Compose Bar** (`#ffffff`): Message composition area with 1px `#dddddd` border.

### Neutrals & Text
- **Primary Text** (`#1d1c1d`): Message body text, headings -- near-black with a micro-warm cast.
- **Secondary Text** (`#616061`): Timestamps, channel topic, metadata, placeholder text.
- **Muted Text** (`#868686`): Disabled states, tertiary labels, "typing..." indicators.
- **Sidebar Text** (`#ffffff`): Channel names on aubergine, full white for readability.
- **Sidebar Muted** (`rgba(255,255,255,0.7)`): Unread-but-not-mentioned channels on sidebar.
- **Divider** (`#e8e8e8`): Horizontal rules between date groups in the message pane.
- **Border** (`#dddddd`): Input borders, card outlines, compose box frame.

### Accessible Text Variants
- **Accessible Cyan Text** (`#0080a8`): Darker variant of Slack Blue for use as text on white/light surfaces. Passes WCAG AA (4.50:1). Use instead of `#36c5f0` whenever cyan needs to appear as readable body or label text.

### Semantic & Accent
- **Link Blue** (`#1264a3`): Hyperlinks in message body -- darker, accessible blue.
- **Mention Highlight** (`#fff3c4`): Warm yellow wash behind messages that @mention you.
- **Code Background** (`#f4f4f4`): Inline code and code block backgrounds.
- **Reaction Pill Bg** (`#f0f0f0`): Default emoji reaction pill background.
- **Reaction Pill Active** (`#e0eefa`): Reaction pill when you have reacted (blue-tinted).

### Gradient System
- **Aubergine Hero**: `linear-gradient(135deg, #611f69 0%, #4a154b 100%)` -- marketing hero sections.
- **Brand Radial**: `radial-gradient(circle at 30% 40%, #36c5f0 0%, #2eb67d 35%, #ecb22e 65%, #e01e5a 100%)` -- the four-color logo gradient used in loading states and celebrations.
- **Sidebar Depth**: `linear-gradient(180deg, #4a154b 0%, #3e103f 100%)` -- subtle vertical darkening on tall sidebars.

## 3. Typography Rules

### Font Family
- **Primary / UI**: `Lato, "Slack-Lato", "Helvetica Neue", Helvetica, "Segoe UI", Tahoma, Arial, sans-serif`
- **Code / Mono**: `"Monaco", "Menlo", "Consolas", "Courier New", monospace`
- **Display (Marketing)**: `Inter, "Helvetica Neue", Helvetica, Arial, sans-serif` -- used on slack.com, not in the product app

### Hierarchy

| Role | Font | Size | Weight | Line Height | Letter Spacing | Notes |
|------|------|------|--------|-------------|----------------|-------|
| Marketing Hero | Inter | 56px (3.5rem) | 800 | 1.05 | -0.02em | Landing page headlines |
| Marketing Sub | Inter | 24px (1.5rem) | 400 | 1.4 | -0.01em | Hero subtitles, marketing body |
| Page Heading | Lato | 28px (1.75rem) | 900 | 1.2 | normal | Settings page titles, modal headers |
| Section Heading | Lato | 22px (1.375rem) | 700 | 1.3 | normal | Sidebar workspace name, preferences sections |
| Channel Name | Lato | 15px (0.9375rem) | 700 | 1.33 | normal | `# general`, bold in header bar |
| Message Body | Lato | 15px (0.9375rem) | 400 | 1.46 | normal | Standard chat message text |
| Username | Lato | 15px (0.9375rem) | 700 | 1.33 | normal | Message author display name |
| Timestamp | Lato | 12px (0.75rem) | 400 | 1.33 | normal | "11:42 AM", hover-reveal on messages |
| Sidebar Channel | Lato | 15px (0.9375rem) | 400 | 1.33 | normal | Unread: weight 700, white text |
| Sidebar Section | Lato | 15px (0.9375rem) | 700 | 1.33 | 0.5px | "CHANNELS", "DIRECT MESSAGES" -- uppercase |
| Code Inline | Monaco | 12px (0.75rem) | 400 | 1.5 | normal | Backtick-wrapped `code` in messages |
| Code Block | Monaco | 13px (0.8125rem) | 400 | 1.5 | normal | Triple-backtick ``` blocks |
| Caption / Meta | Lato | 13px (0.8125rem) | 400 | 1.38 | normal | Thread reply count, file metadata |

### Principles
- **15px is sacred**: Message body never changes from 15px. Density comes from line-height (1.46) and group spacing, not font size.
- **Bold for identity**: Usernames, channel names, and workspace names use weight 700/900 to create anchor points in a flowing text stream.
- **Uppercase sparingly**: Only sidebar section headers ("CHANNELS", "DIRECT MESSAGES") use uppercase with positive letter-spacing. Everything else is sentence case.
- **System-native feel**: Lato's round terminals match the approachable, tool-not-toy personality Slack cultivates.

## 4. Component Stylings

### Buttons

**Primary (Green)**
- Background: `#007a5a`
- Text: `#ffffff`
- Padding: 8px 16px
- Radius: 8px
- Hover: `#005e45`
- Use: "Send", "Save", "Create Channel" -- affirmative actions

**Secondary (Outline)**
- Background: `#ffffff`
- Text: `#1d1c1d`
- Padding: 8px 16px
- Radius: 8px
- Border: 1px solid `#dddddd`
- Hover: background `#f8f8f8`
- Use: "Cancel", "Maybe Later", secondary actions

**Danger**
- Background: `#e01e5a`
- Text: `#ffffff`
- Padding: 8px 16px
- Radius: 8px
- Hover: `#c91a50`
- Use: "Delete", "Leave Channel", destructive actions

**Link-style**
- Background: transparent
- Text: `#1264a3`
- Padding: 0
- Hover: underline, text `#0b4c8c`
- Use: Inline actions, "View thread", "Mark as read"

### Cards
- Background: `#ffffff`
- Border: 1px solid `#e8e8e8`
- Radius: 8px
- Shadow: `0 1px 3px rgba(0,0,0,0.08)`
- Padding: 16px
- Use: Shared links (unfurl previews), file attachments, app cards

### Inputs
- Background: `#ffffff`
- Text: `#1d1c1d`
- Border: 1px solid `#dddddd`
- Radius: 8px
- Padding: 8px 12px
- Focus: border `#1264a3`, box-shadow `0 0 0 1px #1264a3`
- Placeholder: `#868686`

### Navigation

**Workspace Switcher (Far-left Rail)**
- Width: 70px, background: `#3e103f` (slightly darker than sidebar)
- Workspace icons: 36x36px, radius 8px (rounded square)
- Active indicator: 3px white pill on the left edge
- Add workspace: `+` icon, `rgba(255,255,255,0.3)` border

**Channel Sidebar**
- Width: 260px, background: `#4a154b` (aubergine)
- Workspace name: 18px weight 900, white, top of sidebar
- Section headers: 13px weight 700, `rgba(255,255,255,0.7)`, uppercase
- Channel rows: 28px height, 15px text, `rgba(255,255,255,0.7)` default
- Unread channels: white text, weight 700
- Mention badge: `#e01e5a` background, white text, pill shape, right-aligned
- Hover: `rgba(255,255,255,0.1)` background
- Selected: `rgba(255,255,255,0.18)` background, white text

### Distinctive Components

**Message Bubble**
- No bubble -- messages are flat rows on white background
- Avatar: 36x36px, radius 4px (slightly rounded square)
- Username + timestamp on first line, message body below
- Group spacing: 4px between consecutive messages from same user, 16px between different users
- Hover: light gray (`#f8f8f8`) background, action toolbar appears top-right
- Action toolbar: emoji, thread, bookmark, share, more -- 28px icon buttons, `#ffffff` bg, `1px solid #e8e8e8`, 8px radius

**Thread Panel**
- Width: 400px, slides in from the right
- Background: `#ffffff`, separated by 1px `#e8e8e8` left border
- Header: channel name + "Thread" label, close button
- Reply input at bottom with same compose bar styling as main pane

**Emoji Reactions**
- Container: inline row below message, flex-wrap
- Pill shape: 20px height, radius 12px, padding 0 8px
- Default: `#f0f0f0` background, `#1d1c1d` text
- User-reacted: `#e0eefa` background, `1px solid #1264a3` border
- Emoji: 16px size, count in 12px beside it
- Hover: darker background, cursor pointer
- Add reaction button: `+` icon in same pill style, dashed border

**Compose Bar**
- Position: bottom of message pane, sticky
- Background: `#ffffff`
- Border: 1px solid `#dddddd`, radius 8px
- Toolbar row: bold, italic, strikethrough, link, lists, code, emoji, attach, shortcuts
- Placeholder: "Message #channel-name" in `#868686`
- Send button: green (`#007a5a`), appears when text is entered

**Huddle UI**
- Trigger: headphone icon in sidebar footer
- Active state: green pill indicator with participant avatars
- Mini player: 36px avatar stack, green ring around active speakers
- Screen share: golden-yellow (`#ecb22e`) ring indicator

## 5. Layout Principles

### Spacing System
- Base unit: 4px. Scale: 4, 8, 12, 16, 20, 24, 32, 48.
- Message group gap: 16px between different senders.
- Same-sender gap: 4px between consecutive messages.
- Sidebar row height: 28px with 4px vertical padding.
- Section gap in sidebar: 16px between channel groups.

### Grid & Structure
- **Workspace rail**: 70px fixed, far left
- **Channel sidebar**: 260px, resizable (220px min, 400px max)
- **Message pane**: fluid, min 400px
- **Thread panel**: 400px, overlays or pushes message pane
- **Total minimum**: ~730px viewport for usable desktop layout

### Whitespace Philosophy
- **Density over decoration**: Slack is a work tool used 8+ hours a day. Every pixel of vertical space matters -- messages are tight, metadata is compact, and ornamentation is near zero.
- **Horizontal breathing room**: While vertical space is compressed, horizontal padding in the message pane is generous (20px side padding, 72px left gutter for avatars).
- **The sidebar is the brand**: The only place aubergine and color personality exist is the sidebar. The message pane is intentionally austere so content (user messages) takes precedence.

### Border Radius Scale
- Subtle (4px): Message avatars, workspace icons in compose
- Standard (8px): Buttons, inputs, cards, compose bar, workspace icons
- Comfortable (12px): Emoji reaction pills, tooltip containers
- Full Pill (9999px): Mention badges, status dots, notification counts
- Circle (50%): User status dots (online/away/DND), huddle speaker rings

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Flat (Level 0) | No shadow | Message rows, sidebar, most surfaces |
| Whisper (Level 1) | `0 1px 3px rgba(0,0,0,0.08)` | Unfurl cards, file previews, app cards |
| Raised (Level 2) | `0 4px 12px rgba(0,0,0,0.12)` | Action toolbar on message hover, tooltips |
| Float (Level 3) | `0 8px 24px rgba(0,0,0,0.15)` | Modals, popovers, emoji picker, channel browser |
| Overlay (Level 4) | `0 12px 36px rgba(0,0,0,0.2)` + `rgba(0,0,0,0.5)` scrim | Full-screen modals, image lightbox, onboarding overlays |

**Shadow Philosophy**: Slack is a flat interface -- Level 0 dominates. Shadows appear only when something floats above the conversation: the hover action toolbar, the emoji picker, modal dialogs. The transition from flat to elevated is always user-triggered (hover, click), never ambient. This keeps the reading surface calm.

## 7. Do's and Don'ts

### Do
- Use the aubergine sidebar as the primary brand moment -- it defines the workspace personality
- Keep the message pane white and unadorned -- messages are the content, not the chrome
- Use the four brand colors (blue, green, yellow, red) as small accents: dots, badges, pills -- never as backgrounds
- Set message text at 15px Lato weight 400 -- this is non-negotiable for the chat reading experience
- Use green (`#007a5a`) for primary affirmative actions and red (`#e01e5a`) for destructive ones
- Show hover-to-reveal interactions: message actions, timestamps, thread counts
- Maintain the three-column layout: rail + sidebar + content (+ optional thread panel)
- Use `#1264a3` for in-message links -- it is a distinct, accessible blue separate from brand blue
- Use Accessible Cyan (`#0080a8`) when blue needs to appear as readable text on white surfaces

### Don't
- Don't use aubergine as a background outside the sidebar -- it is a navigation color, not a surface color
- Don't make message bubbles -- Slack messages are flat rows, not chat bubbles
- Don't use the brand colors (blue `#36c5f0`, green `#2eb67d`) for buttons or large surfaces -- they are accent-only
- Don't add shadows to the message pane -- the reading surface must stay perfectly flat
- Don't change message body size from 15px -- density comes from spacing, not font scaling
- Don't use uppercase outside sidebar section headers -- Slack's tone is conversational, not formal
- Don't put color on the compose bar background -- it stays white with a simple `#dddddd` border
- Don't use heavy border weights -- 1px `#e8e8e8` or `#dddddd` is the maximum
- Don't use Slack Blue (`#36c5f0`) as text on white -- it fails WCAG AA. Reserve it for icons, borders, and filled backgrounds with dark text

## 8. Responsive Behavior

### Breakpoints
| Name | Width | Key Changes |
|------|-------|-------------|
| Mobile | <768px | Single pane, sidebar as overlay drawer, no thread panel |
| Tablet | 768-1024px | Sidebar visible, thread panel overlays message pane |
| Desktop | 1024-1440px | Full three-column layout, thread panel pushes content |
| Large Desktop | >1440px | Generous message pane width, centered content cap at ~900px |

### Collapsing Strategy
- **Mobile**: Sidebar becomes a slide-out drawer from left. Only one pane visible at a time (channels list OR conversation OR thread). Bottom tab bar for Home, DMs, Mentions, Search.
- **Tablet**: Sidebar persists but narrows. Thread panel overlays rather than pushing.
- **Desktop**: Full workspace rail + sidebar + message pane + optional thread panel.
- **Message text**: 15px at all breakpoints -- never scales down.
- **Compose bar**: Full-width at bottom, toolbar icons collapse into overflow menu on narrow viewports.

### Touch Targets
- Sidebar channel rows: 28px minimum height with full-width tap target
- Message action buttons: 44px tap target (28px visible + padding)
- Compose toolbar icons: 36px tap targets
- Emoji reaction pills: 32px minimum tap area

## 9. Agent Prompt Guide

### Quick Color Reference
- Sidebar background: Aubergine Dark (`#4a154b`)
- Message surface: White (`#ffffff`)
- Primary text: Near-Black (`#1d1c1d`)
- Secondary text: Gray (`#616061`)
- Primary CTA: Green (`#007a5a`)
- Destructive: Red (`#e01e5a`)
- Links: Blue (`#1264a3`)
- Mention highlight: Yellow Wash (`#fff3c4`)
- Dividers: Light Gray (`#e8e8e8`)
- Borders: Gray (`#dddddd`)
- Brand accent 1: Blue (`#36c5f0`)
- Brand accent 2: Green (`#2eb67d`)
- Brand accent 3: Yellow (`#ecb22e`)
- Brand accent 4: Red (`#e01e5a`)
- Brand primary: Aubergine (`#611f69`)

### Example Component Prompts
- "Create a Slack-style channel sidebar on `#4a154b` aubergine background, 260px wide. Workspace name at top: 18px Lato weight 900, white text. Section headers: 13px weight 700, `rgba(255,255,255,0.7)`, uppercase, 0.5px letter-spacing. Channel rows: 28px height, 15px weight 400, `rgba(255,255,255,0.7)` text with `#` prefix. Unread channels: white text, weight 700. Red mention badge (`#e01e5a`, white text, pill shape) right-aligned on rows with mentions."
- "Design a message row: white background, no bubble. 36px avatar (4px radius) left-aligned with 12px gap. Username at 15px Lato weight 700, timestamp at 12px weight 400 `#616061` beside it. Message body at 15px weight 400, `#1d1c1d`, line-height 1.46. On hover: `#f8f8f8` background, action toolbar (emoji, thread, bookmark, more) floats top-right with `0 4px 12px rgba(0,0,0,0.12)` shadow."
- "Build an emoji reaction row: flex-wrap row of pills. Each pill 20px tall, 12px radius, `#f0f0f0` background, emoji at 16px + count at 12px. User-reacted variant: `#e0eefa` background, `1px solid #1264a3` border. Final pill is `+` add reaction with dashed border."
- "Create a compose bar: bottom-sticky, `#ffffff` background, `1px solid #dddddd` border, 8px radius. Placeholder 'Message #general' in `#868686`. Toolbar row below: bold/italic/strike/link/list/code icons at 20px in `#616061`. Green send button (`#007a5a`, white arrow icon) appears when input has content."
- "Design a workspace switcher rail: 70px wide, `#3e103f` background. Workspace icons at 36x36px with 8px radius (rounded square). Active workspace has 3px white pill on the left edge. Bottom: `+` add workspace button in `rgba(255,255,255,0.3)` dashed border circle."

### Iteration Guide
1. The sidebar is aubergine (`#4a154b`), the message pane is white -- this division is the entire visual identity. Never blend them.
2. Message text is always 15px Lato weight 400 -- change spacing, never font size.
3. Brand colors (blue `#36c5f0`, green `#2eb67d`, yellow `#ecb22e`, red `#e01e5a`) are for dots, badges, and small moments -- never for backgrounds or large surfaces.
4. Primary action buttons are green (`#007a5a`), not aubergine or brand blue.
5. Shadows only appear on floating elements triggered by user interaction (hover toolbar, emoji picker, modals). The reading surface is flat.
6. Borders are 1px maximum: `#e8e8e8` for dividers, `#dddddd` for inputs and cards.
7. The three-column structure (rail + sidebar + pane) is the skeleton -- even on a simple mockup, hint at this structure for Slack authenticity.
