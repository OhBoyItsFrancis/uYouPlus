#!/usr/bin/env bash
# Builds an unsigned Chromium (Blink) IPA with ad blocking, on a Mac.
#
# Uses the Mr-Drinking/Chromium-iOS sideload project (pinned below) for the
# Chromium checkout, its 38 sideload patches and its build/packaging
# scripts, and adds patch 0039 (ad blocking) plus freshly compiled
# uBlock Origin lists.
#
# Requirements: Apple Silicon Mac, Xcode 26+ (27 recommended), ~200 GB free.
#
# usage: build-ipa.sh [--sim]
#   Environment (all optional):
#     WORK_DIR          where everything goes      (default: ~/chromium-blink)
#     BUNDLE_ID_PREFIX  your reverse-DNS prefix   (default: com.example.iosblink)
#     BUILD_JOBS        parallel compile jobs     (default: one per core)
#   --sim  build and run in the iOS Simulator instead of making the IPA.
set -euo pipefail

SIDELOAD_REPO="https://github.com/Mr-Drinking/Chromium-iOS.git"
SIDELOAD_REV="7a9824d1006f7326235d42d8c2d2d1ddb847c2e2"  # Chromium 159.0.8129.0

here="$(cd "$(dirname "$0")" && pwd)"
patch_0039="$here/../patches/0039-ios-sideload-adblock.patch"
WORK_DIR="${WORK_DIR:-$HOME/chromium-blink}"
export BUNDLE_ID_PREFIX="${BUNDLE_ID_PREFIX:-com.example.iosblink}"
export CHROMIUM_DIR="${CHROMIUM_DIR:-$WORK_DIR/chromium}"
export DEPOT_TOOLS_DIR="${DEPOT_TOOLS_DIR:-$WORK_DIR/depot_tools}"
mode=device
[ "${1:-}" = "--sim" ] && mode=sim

[ "$(uname)" = Darwin ] || { echo "error: needs macOS with Xcode" >&2; exit 1; }
mkdir -p "$WORK_DIR"

# 1. The sideload project, pinned.
project="$WORK_DIR/Chromium-iOS"
if [ ! -d "$project/.git" ]; then
  git clone "$SIDELOAD_REPO" "$project"
fi
git -C "$project" fetch -q origin "$SIDELOAD_REV" || true
git -C "$project" checkout -q --detach "$SIDELOAD_REV"
git -C "$project" checkout -q -- patches args  # undo edits from a previous run

# 2. Add patch 0039 to its series.
cp "$patch_0039" "$project/patches/"
grep -qx '0039-ios-sideload-adblock.patch' "$project/patches/series" ||
  echo '0039-ios-sideload-adblock.patch' >> "$project/patches/series"

# 3. Compile the filter lists and point the build at them.
lists_dir="$WORK_DIR/adblock-lists"
"$here/fetch-lists.sh" "$lists_dir"
for args_file in "$project/args/device.gn" "$project/args/simulator.gn"; do
  printf '\n# Ad blocking (patch 0039): compiled uBlock Origin lists.\nios_sideload_adblock_lists_dir = "%s"\n' \
    "$lists_dir" >> "$args_file"
done

# 4. Fetch Chromium, apply 0001-0039, build.
cd "$project"
scripts/00-check-env.sh
scripts/01-fetch-chromium.sh
scripts/02-apply-patches.sh
if [ "$mode" = sim ]; then
  scripts/03-build.sh sim
  scripts/04-run-simulator.sh
else
  scripts/03-build.sh device
  scripts/05-package-ipa.sh
  echo "IPA: $(ls -t "$project"/dist/*.ipa | head -1)"
fi
