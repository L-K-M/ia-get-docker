#!/bin/sh
# Verify the license notices inside a built, locally loaded ia-get-docker
# image. CI and the release workflow run this before anything is pushed:
#   1. each notice file names components certain to be bundled, so an empty
#      or stale file fails, and the web UI's own copy exists;
#   2. scripts/licenses/check-image-notices.sh: every file in the doc dir
#      exists and is not empty, and the image's
#      org.opencontainers.image.licenses label matches the licenses the
#      notices name. On a mismatch it prints the expected expression: copy
#      it into the Dockerfile's LABEL.
#
#   sh scripts/verify-image-notices.sh IMAGE:TAG
set -eu

[ $# -eq 1 ] || { echo "usage: $0 IMAGE:TAG" >&2; exit 2; }
image=$1
doc=/usr/share/doc/ia-get-docker
web=/app/web

# Sentinels: ia-get and system7-ui's OFL fonts in the hand-written file; a
# crate certain to ship, the MPL-2.0 crate and the fonts the README names,
# Flask, CPython and Svelte in the generated file.
docker run --rm --entrypoint sh "$image" -euc '
  doc=$1 web=$2
  check() {
    grep -q "$2" "$1" || { echo "error: $1 does not mention $2" >&2; exit 1; }
  }
  test -s "$web/Third-Party-Notices.txt"
  check "$web/Third-Party-Notices.txt" "^svelte "
  check "$doc/THIRD_PARTY_NOTICES.md" "Martin Wimpress"
  check "$doc/THIRD_PARTY_NOTICES.md" "SIL OPEN FONT LICENSE Version 1.1"
  for name in "^tokio " "^colored " "^Flask " "^CPython " "^svelte " Geneva Sysfont; do
    check "$doc/Third-Party-Notices.txt" "$name"
  done
  check "$doc/Rust-Licenses.html" "Copyright notices for The Rust Standard Library"
  check "$doc/OS-Packages.txt" "^Package: tini$"
' sh "$doc" "$web"

# Rust-Licenses.html and THIRD_PARTY_NOTICES.md are not in the generators'
# format, so their terms are not read from them. THIRD_PARTY_NOTICES.md's
# (MIT, Unlicense, OFL-1.1) also come from the generated notices; the
# standard library's terms that no crate brings are added here (from
# COPYRIGHT-library.html of rustc 1.99). tini, installed for PID 1, is MIT
# (Expat in its Debian copyright file).
exec sh "$(dirname "$0")/licenses/check-image-notices.sh" "$image" "$doc" \
  LICENSE THIRD_PARTY_NOTICES.md Third-Party-Notices.txt Rust-Licenses.html OS-Packages.txt \
  -- --own MIT \
  --package tini=MIT \
  --add 'Apache-2.0 OR Apache-2.0 WITH LLVM-exception OR MIT' \
  --add 'Apache-2.0 OR BSD-2-Clause OR MIT' \
  --add 'Apache-2.0 OR LGPL-2.1-or-later OR MIT' \
  --add BSD-2-Clause \
  --add Zlib
