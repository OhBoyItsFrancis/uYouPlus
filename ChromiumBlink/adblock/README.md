# Chromium (Blink) for iOS: ad and content blocking

Patch `0039-ios-sideload-adblock.patch` adds ad, tracker and malware blocking to
the sideloadable Blink build of Chromium from
[Mr-Drinking/Chromium-iOS](https://github.com/Mr-Drinking/Chromium-iOS)
(Chromium 159.0.8129.0 + patches 0001–0038). It uses the filter lists that
uBlock Origin enables by default.

> **Status: written but not yet compiled.** The patch applies cleanly on top
> of 0001–0038, and the list compiler was tested on the real lists. But no
> one has built it yet, because that needs a Mac. Expect possibly a few
> compile fixes on the first build.

## How it works

Everything runs on Chromium's own ad-blocking engine, the
[subresource filter](https://chromium.googlesource.com/chromium/src/+/main/components/subresource_filter/README.md).
Chrome normally switches it on only for sites that Safe Browsing flags; this
patch switches it on for every page.

| Piece | What it does |
|---|---|
| `scripts/compile_filters.py` (build time) | Turns uBO lists into the Adblock Plus syntax Chromium's parser understands. It evaluates `!#if` blocks for Chromium/mobile, applies `$badfilter`, and maps uBO option aliases to ABP names (`3p`, `1p`, `xhr`, `frame`, `from=`, …). Rules it can't enforce exactly are **dropped, never broadened**. It writes three files: the rules, a generic hiding stylesheet, and per-site exceptions to that stylesheet. |
| `AdblockRuleset` (first launch) | Parses the bundled lists with Chromium's EasyList parser and indexes them with `RulesetIndexer`. The index is cached in `Library/Caches/SideloadAdblock` and verified with a checksum on later launches. It is rebuilt when the lists or the Chromium version change. |
| `AdblockRenderFrameObserver` | Installs a Blink subresource filter in every web page, iframe and worker, so matching scripts, images, XHR/fetch, media, fonts and WebSockets are blocked. Also inserts a user-origin `!important` stylesheet that hides ad placeholders: site-specific `##` rules everywhere, generic rules in the main page. |
| `AdblockNavigationThrottle` | Blocks matching iframes and collapses their space. |
| chrome://flags | **Ad and content blocking** and **Ad blocking: hide ad placeholders**. Both are on by default. |

`@@…$document`, `$genericblock`, `$elemhide` and `$generichide` exceptions
are respected, and child frames inherit them.

### Lists (uBO defaults, downloaded fresh at every build)

uBlock filters (Ads, Badware risks, Privacy, Quick fixes, Unbreak),
EasyList, EasyPrivacy, Peter Lowe's list, and the Online Malicious URL
Blocklist. Together these produce about 232,000 network rules and 14,000
generic plus 15,000 site-specific hiding rules. To change the set, edit
`LISTS` in `scripts/fetch-lists.sh`. The lists are not stored in this
repository; each one keeps its own license (uBlock filters: GPL-3.0;
EasyList/EasyPrivacy: GPL-3.0 or CC BY-SA 3.0).

### What uBlock Origin does that this doesn't

These features need uBO's extension runtime, so the compiler skips rules that
use them. On the current lists that's about 15,000 of 270,000 rules:

- scriptlets (`##+js(...)`); this matters most on YouTube and anti-adblock sites;
- HTML filtering (`##^`) and procedural cosmetic filters (`:has-text()`,
  `:upward()`, …). Plain CSS `:has()` is kept;
- `$redirect`, `$removeparam`, `$csp`, `$denyallow`, `$header`, regex
  filters, entity domains (`google.*`), and the warning page uBO shows
  before opening a blocked site;
- a per-site on/off switch. To turn blocking off entirely, use chrome://flags.

## Build (on a Mac)

Requirements: an Apple Silicon Mac, Xcode 26 or later (27 recommended) and
about 200 GB of free disk space. The first full build takes several hours.

```bash
BUNDLE_ID_PREFIX=com.yourname.chromium ChromiumBlink/adblock/scripts/build-ipa.sh
```

The script:

1. clones Mr-Drinking/Chromium-iOS at the pinned commit;
2. adds patch 0039 to its series;
3. downloads and compiles the lists, then sets
   `ios_sideload_adblock_lists_dir` in its GN args;
4. runs that project's scripts to fetch Chromium, apply 0001–0039, build
   for device and package `dist/Chromium-159.0.8129.0-sideload.ipa`
   (unsigned).

Use `--sim` to build and launch it in the iOS Simulator instead. It's worth
doing this first, because simulator builds keep debug checks (DCHECKs)
turned on.

Sign and install the IPA as before (Sideloadly, ESign or Xcode with your
developer account; Developer Mode on).

### Checking it works

- Open `https://d3ward.github.io/toolz/adblock` or
  `https://adblock-tester.com`.
- Attach Safari's Web Inspector or `chrome://inspect`
  (`--remote-debugging-port`): blocked requests fail with
  `net::ERR_BLOCKED_BY_CLIENT`.
- The first launch after installing builds the index in a background task
  (roughly 1–3 s, an estimate). Pages that load before it finishes aren't
  filtered. Later launches reuse the cached index.

## Files

```
patches/0039-ios-sideload-adblock.patch   the Chromium patch
scripts/compile_filters.py               uBO lists -> Chromium-compatible files
scripts/fetch-lists.sh                   download + compile the default lists
scripts/build-ipa.sh                     end-to-end Mac build
```
