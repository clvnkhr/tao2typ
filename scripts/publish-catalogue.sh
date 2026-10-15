#!/usr/bin/env bash
# Publish the page through an existing checkout of the personal Pages site.
set -euo pipefail
cd "$(dirname "$0")/.."
site_checkout="${1:?Usage: bash scripts/publish-catalogue.sh /path/to/clvnkhr.github.io}"
publish_downloads="${2:-}"
if [[ -n "$publish_downloads" && "$publish_downloads" != "--public-downloads" ]]; then
  echo "Optional second argument: --public-downloads" >&2
  exit 1
fi
site_checkout="$(cd "$site_checkout" && pwd)"
site_origin="$(git -C "$site_checkout" remote get-url origin)"
case "$site_origin" in
  https://github.com/clvnkhr/clvnkhr.github.io.git|https://github.com/clvnkhr/clvnkhr.github.io|git@github.com:clvnkhr/clvnkhr.github.io.git) ;;
  *) echo "Expected the clvnkhr.github.io repository." >&2; exit 1 ;;
esac
if [[ "$(git -C "$site_checkout" branch --show-current)" != "master" ]]; then
  echo "The Pages checkout must be on master." >&2
  exit 1
fi
site_output="docs"
if [[ "$publish_downloads" == "--public-downloads" ]]; then
  release_output="$(mktemp -d "${TMPDIR:-/tmp}/tao2typ-release.XXXXXX")"
  trap 'rm -rf -- "$release_output"' EXIT
  site_output="$release_output"
fi
site_args=(site --archive archive --output "$site_output")
if [[ "$publish_downloads" == "--public-downloads" ]]; then
  site_args+=(--download-base-url https://clvnkhr.github.io/tao2typ)
fi
uv run --frozen tao2typ "${site_args[@]}"
mkdir -p "$site_checkout/public/tao2typ"
# Development publishes only the page. The explicit release flag adds downloads.
for site_asset in index.html faq.html style.css app.js .nojekyll; do
  cp "$site_output/$site_asset" "$site_checkout/public/tao2typ/$site_asset"
done
if [[ "$publish_downloads" == "--public-downloads" ]]; then
  for download_dir in pdfs typst ir assets; do
    if [[ -d "$site_output/$download_dir" ]]; then
      mkdir -p "$site_checkout/public/tao2typ/$download_dir"
      cp -R "$site_output/$download_dir/." "$site_checkout/public/tao2typ/$download_dir/"
    fi
  done
fi
git -C "$site_checkout" add -- public/tao2typ
if git -C "$site_checkout" diff --cached --quiet -- public/tao2typ; then exit 0; fi
git -C "$site_checkout" commit --quiet --only -m "Update tao2typ page" -- public/tao2typ
git -C "$site_checkout" pull --rebase origin master
git -C "$site_checkout" push origin HEAD:master
