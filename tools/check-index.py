#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check index.json as a box would, before it is published.

- the public key (default: webui-index.pub beside the index, the key the
  images carry) is an Ed25519 key, and index.json.sig holds to index.json
  under it;
- the index is strict JSON of format 1 and every entry has the fields the
  box asks for (tools/indexrules.py);
- every archive an entry names lies in this repository at the place its
  address points to, has the entry's SHA-256, and is one the box installs:
  the folder <id>/ with a manifest of the entry's version, nothing but
  folders and regular files.

Exit status 0 when all of it holds, 1 otherwise, with one line per finding.
"""

import argparse
import base64
import binascii
import hashlib
import os
import subprocess
import sys
import tempfile
import urllib.parse

from indexrules import MAX_INDEX, Refused, check_archive, entry_problem, format_one, strict_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = 'https://raw.githubusercontent.com/tuxbox-neutrino/neutrino-webui-ext/HEAD/'


def ed25519_public(key_path):
	r = subprocess.run(['openssl', 'pkey', '-pubin', '-in', key_path, '-noout', '-text'],
			   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
	return r.returncode == 0 and r.stdout.startswith(b'ED25519 Public-Key')


def signature_holds(index_path, sig_path, key_path):
	"""None where the signature holds, else why not."""
	if not ed25519_public(key_path):
		return '%s is no Ed25519 public key, the only kind the box checks with' % key_path
	with open(sig_path, 'rb') as f:
		text = b''.join(f.read().split())
	try:
		raw = base64.b64decode(text, validate=True)
	except (binascii.Error, ValueError):
		return 'the signature is no base64'
	if len(raw) != 64:
		return 'the signature is not 64 bytes in base64'
	with tempfile.NamedTemporaryFile() as s:
		s.write(raw)
		s.flush()
		r = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', key_path, '-rawin',
				    '-in', index_path, '-sigfile', s.name],
				   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	return None if r.returncode == 0 else 'the index does not hold to its signature under ' + key_path


def check_entry(i, e, base, seen, problems):
	problem = entry_problem(e)
	if problem:
		problems.append('entry %d %s' % (i + 1, problem))
		return
	ext = e['id']
	if ext in seen:
		problems.append('the index lists %s twice' % ext)
	seen.add(ext)
	url = urllib.parse.urlsplit(e['url'])
	b = urllib.parse.urlsplit(base)
	if (url.scheme, url.netloc) != (b.scheme, b.netloc) or not url.path.startswith(b.path):
		problems.append('%s: %s does not point into this repository' % (ext, e['url']))
		return
	path = os.path.join(ROOT, urllib.parse.unquote(url.path[len(b.path):]))
	if not os.path.isfile(path):
		problems.append('%s: %s is missing here' % (ext, os.path.relpath(path, ROOT)))
		return
	with open(path, 'rb') as f:
		blob = f.read()
	if hashlib.sha256(blob).hexdigest() != e['sha256'].lower():
		problems.append('%s: %s does not have the SHA-256 the index gives' % (ext, os.path.relpath(path, ROOT)))
		return
	try:
		m = check_archive(blob, ext)
	except Refused as x:
		problems.append(str(x))
		return
	if m['version'] != e['version']:
		problems.append('%s: the archive\'s manifest says version %s, the index %s' % (ext, m['version'], e['version']))


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument('--key', default=os.path.join(ROOT, 'webui-index.pub'), help='public key, PEM (default: %(default)s)')
	ap.add_argument('--signature', default=os.path.join(ROOT, 'index.json.sig'), help='signature to check (default: %(default)s)')
	ap.add_argument('--base-url', default=DEFAULT_BASE, help='the address the entries begin with (default: %(default)s)')
	args = ap.parse_args()

	index_path = os.path.join(ROOT, 'index.json')
	problems = []
	seen = set()
	try:
		why = signature_holds(index_path, args.signature, args.key)
		if why:
			problems.append(why)
		with open(index_path, 'rb') as f:
			data = f.read()
		if len(data) > MAX_INDEX:
			problems.append('the index is larger than 256 KiB')
		idx = strict_json(data.decode('utf-8-sig'), 'index.json')
		if not format_one(idx.get('format')) or not isinstance(idx.get('extensions'), list):
			problems.append('the index is no object of format 1 with a list of extensions')
		else:
			for i, e in enumerate(idx['extensions']):
				check_entry(i, e, args.base_url, seen, problems)
	except (Refused, OSError, UnicodeError, ValueError) as e:
		problems.append(str(e))
	for p in problems:
		print('check-index: ' + p, file=sys.stderr)
	if problems:
		return 1
	print('check-index: key, signature, form and %d archives hold' % len(seen))
	return 0


if __name__ == '__main__':
	sys.exit(main())
