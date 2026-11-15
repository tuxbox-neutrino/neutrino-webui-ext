#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Signs index.json as the box checks it: Ed25519 over the file's bytes,
# base64 in index.json.sig. The new signature replaces the old one only
# once check-index.py has found it to hold under webui-index.pub, so a
# failed run leaves the published signature as it was.
#   tools/sign-index.sh <private key, PEM>
# The private key never belongs in this repository.
set -eu
[ $# -eq 1 ] || { echo "usage: $0 <private key, PEM>" >&2; exit 2; }
key=$1
cd "$(dirname "$0")/.."
tmp=$(mktemp index.json.sig.XXXXXX)
trap 'rm -f "$tmp" "$tmp.raw"' EXIT
# each step on its own, so a failure of one stops the run
if ! openssl pkeyutl -sign -rawin -inkey "$key" -in index.json -out "$tmp.raw"; then
	echo "sign-index: openssl could not sign with $key" >&2
	exit 1
fi
if ! base64 -w0 "$tmp.raw" > "$tmp"; then
	echo "sign-index: base64 failed" >&2
	exit 1
fi
python3 tools/check-index.py --signature "$tmp"
mv "$tmp" index.json.sig
