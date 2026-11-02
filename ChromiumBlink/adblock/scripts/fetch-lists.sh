#!/usr/bin/env bash
# Downloads uBlock Origin's default filter lists and compiles them for the
# Chromium iOS adblock patch (0039).
#
# usage: fetch-lists.sh OUT_DIR
#   -> OUT_DIR/adblock_filters.txt, adblock_generic.css,
#      adblock_generic_exceptions.txt
#
# The lists are the ones uBlock Origin enables by default
# (https://github.com/gorhill/uBlock/blob/master/assets/assets.json). Edit
# LISTS below to add or remove some; anything in Adblock Plus / uBO syntax
# works.
set -euo pipefail

out_dir="${1:?usage: $0 OUT_DIR}"
here="$(cd "$(dirname "$0")" && pwd)"

LISTS=(
  # uBlock filters (GPL-3.0)
  "ublock-filters https://ublockorigin.github.io/uAssetsCDN/filters/filters.min.txt"
  "ublock-badware https://ublockorigin.github.io/uAssetsCDN/filters/badware.min.txt"
  "ublock-privacy https://ublockorigin.github.io/uAssetsCDN/filters/privacy.min.txt"
  "ublock-quick-fixes https://ublockorigin.github.io/uAssetsCDN/filters/quick-fixes.min.txt"
  "ublock-unbreak https://ublockorigin.github.io/uAssetsCDN/filters/unbreak.min.txt"
  # EasyList / EasyPrivacy (GPL-3.0 or CC BY-SA 3.0), uBO's mirror
  "easylist https://cdn.jsdelivr.net/gh/uBlockOrigin/uAssetsCDN@main/thirdparties/easylist.txt"
  "easyprivacy https://cdn.jsdelivr.net/gh/uBlockOrigin/uAssetsCDN@main/thirdparties/easyprivacy.txt"
  # Peter Lowe's ad and tracking server list, Adblock Plus format
  "plowe https://pgl.yoyo.org/adservers/serverlist.php?hostformat=adblockplus&showintro=1&mimetype=plaintext"
  # Online Malicious URL Blocklist (URLhaus)
  "urlhaus https://malware-filter.gitlab.io/urlhaus-filter/urlhaus-filter-vivaldi-online.txt"
)

download_dir="$(mktemp -d)"
trap 'rm -rf "${download_dir:?}"' EXIT

files=()
for entry in "${LISTS[@]}"; do
  name="${entry%% *}"
  url="${entry#* }"
  echo "==> downloading $name"
  curl -fsSL --retry 3 --max-time 120 -o "$download_dir/$name.txt" "$url"
  files+=("$download_dir/$name.txt")
done

mkdir -p "$out_dir"
python3 "$here/compile_filters.py" --out-dir "$out_dir" "${files[@]}"
