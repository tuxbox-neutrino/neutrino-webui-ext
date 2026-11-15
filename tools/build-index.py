#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pack Neutrino-Web-UI extensions and write the index that offers them.

Each SOURCE is the folder of one extension, the one holding its
manifest.json, given either as a folder on disk or as REPO:REV:PATH, a
folder in a git repository at a revision, for example

    ~/sources/neutrino:master:data/web-ui/extensions/files

An extension is packed as archives/<id>-<version>.tar.gz, <id> being the
folder's name and <version> the one its manifest names, and its entry in
index.json is added or replaced. Entries of other extensions stay as they
are; --remove takes one out. A published archive does not change: when the
index or the archive folder already holds that version with other bytes,
the extension needs a new version in its manifest.

Every archive is checked with the rules the box installs by before it is
written (tools/indexrules.py). The same files give the same bytes: one
top-level folder, entries sorted by name, owner 0, the modes 0644 and 0755,
as time the last commit that changed the folder (for a folder on disk
SOURCE_DATE_EPOCH, or else the time of its newest file), gzip without a
name or a time. index.json has to be signed again after every change
(tools/sign-index.sh).
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile

from indexrules import (MAX_INDEX, MAX_MEMBERS, MAX_NAME, MAX_PACKED, MAX_UNPACKED, Refused,
			check_archive, check_manifest, entry_problem, extension_name, format_one,
			has_control, https_address, strict_json)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASE = 'https://raw.githubusercontent.com/tuxbox-neutrino/neutrino-webui-ext/HEAD/'


def git(repo, *args, binary=False):
	out = subprocess.run(['git', '-C', repo] + list(args), check=True, stdout=subprocess.PIPE)
	return out.stdout if binary else out.stdout.decode('utf-8')


def files_from_git(repo, rev, path):
	"""(name below the folder, executable, bytes) for every file of REV:PATH, and its time."""
	path = path.strip('/')
	listing = git(repo, 'ls-tree', '-r', '-z', '--full-tree', rev, '--', path + '/')
	files = []
	for item in listing.split('\0'):
		if not item:
			continue
		meta, name = item.split('\t', 1)
		mode, kind, obj = meta.split()
		if kind != 'blob' or mode not in ('100644', '100755'):
			raise Refused('%s:%s is no regular file (mode %s)' % (rev, name, mode))
		files.append((name[len(path) + 1:], mode == '100755', git(repo, 'cat-file', 'blob', obj, binary=True)))
	if not files:
		raise Refused('%s:%s is no folder of the repository' % (rev, path))
	# the last change of the folder, not of the revision: a later revision
	# that leaves the folder alone packs the same bytes
	when = int(git(repo, 'log', '-1', '--format=%ct', rev, '--', path + '/').strip())
	return files, when


def files_from_folder(folder):
	def fail(e):
		raise e
	files = []
	when = 0
	for top, dirs, names in os.walk(folder, onerror=fail):
		for d in dirs:
			if os.path.islink(os.path.join(top, d)):
				raise Refused('%s is a link; an extension is files and folders' % os.path.join(top, d))
		dirs[:] = sorted(d for d in dirs if d not in ('.git', '.github'))
		for n in names:
			p = os.path.join(top, n)
			st = os.lstat(p)
			if os.path.islink(p) or not os.path.isfile(p):
				raise Refused('%s is no regular file' % p)
			with open(p, 'rb') as f:
				files.append((os.path.relpath(p, folder), bool(st.st_mode & 0o111), f.read()))
			when = max(when, int(st.st_mtime))
	fixed = os.environ.get('SOURCE_DATE_EPOCH')
	return files, int(fixed) if fixed else when


def read_source(spec):
	parts = spec.split(':')
	if len(parts) == 3 and os.path.isdir(os.path.expanduser(parts[0])):
		repo, rev, path = parts
		files, when = files_from_git(os.path.expanduser(repo), rev, path)
		return os.path.basename(path.rstrip('/')), files, when
	folder = os.path.expanduser(spec).rstrip('/')
	if not os.path.isdir(folder):
		raise Refused('%s is neither a folder nor REPO:REV:PATH' % spec)
	files, when = files_from_folder(folder)
	return os.path.basename(folder), files, when


def pack(ext, files, when, executable):
	"""The archive's bytes: <ext>/ and everything below it, made the same way each time."""
	dirs = set()
	for rel, _, _ in files:
		parts = rel.split('/')
		for i in range(1, len(parts)):
			dirs.add('/'.join(parts[:i]))
	entries = [(d, None, None) for d in dirs] + list(files)
	entries.sort(key=lambda e: e[0])
	if len(entries) + 1 > MAX_MEMBERS:
		raise Refused('%s holds more than %d names' % (ext, MAX_MEMBERS))
	if sum(len(b) for _, _, b in files) > MAX_UNPACKED:
		raise Refused('%s is larger than 64 MiB unpacked' % ext)
	raw = io.BytesIO()
	with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as tar:
		def add(name, blob=None, mode=0o755):
			if blob is None:
				name += '/'
			if len(name.encode()) > MAX_NAME or has_control(name):
				raise Refused('%s: the name %r is longer than %d bytes or holds a control character' % (ext, name, MAX_NAME))
			ti = tarfile.TarInfo(name)
			ti.mtime = when
			ti.uid = ti.gid = 0
			ti.uname = ti.gname = ''
			if blob is None:
				ti.type = tarfile.DIRTYPE
				ti.mode = 0o755
				tar.addfile(ti)
			else:
				ti.size = len(blob)
				ti.mode = mode
				tar.addfile(ti, io.BytesIO(blob))
		add(ext)
		for rel, exe, blob in entries:
			if blob is None:
				add(ext + '/' + rel)
			else:
				add(ext + '/' + rel, blob, 0o755 if exe or rel in executable else 0o644)
	out = io.BytesIO()
	with gzip.GzipFile(filename='', mode='wb', compresslevel=9, fileobj=out, mtime=0) as gz:
		gz.write(raw.getvalue())
	packed = out.getvalue()
	if len(packed) > MAX_PACKED:
		raise Refused('%s is larger than 16 MiB packed' % ext)
	return packed


def load_index(path):
	if not os.path.exists(path):
		return []
	with open(path, 'rb') as f:
		idx = strict_json(f.read().decode('utf-8-sig'), path)
	if not format_one(idx.get('format')) or not isinstance(idx.get('extensions'), list):
		raise Refused('%s is no index of format 1' % path)
	for i, e in enumerate(idx['extensions']):
		problem = entry_problem(e)
		if problem:
			raise Refused('entry %d of %s %s' % (i + 1, path, problem))
	return idx['extensions']


def write_atomic(path, data):
	tmp = path + '.tmp'
	try:
		with open(tmp, 'wb') as f:
			f.write(data)
			f.flush()
			os.fsync(f.fileno())
		os.replace(tmp, path)
	finally:
		if os.path.exists(tmp):
			os.unlink(tmp)


def main():
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument('sources', nargs='*', metavar='SOURCE')
	ap.add_argument('--remove', action='append', default=[], metavar='ID', help='take this extension out of the index')
	ap.add_argument('--base-url', default=DEFAULT_BASE,
			help='where the repository is read from by a web page, ending in / (default: %(default)s)')
	args = ap.parse_args()
	if not args.base_url.startswith('https://') or not args.base_url.endswith('/'):
		ap.error('--base-url has to be an https address ending in /')

	index_path = os.path.join(ROOT, 'index.json')
	try:
		entries = {e['id']: e for e in load_index(index_path)}
		for ext in args.remove:
			if entries.pop(ext, None) is None:
				raise Refused('the index offers no %s' % ext)
		os.makedirs(os.path.join(ROOT, 'archives'), exist_ok=True)
		for spec in args.sources:
			ext, files, when = read_source(spec)
			if not extension_name(ext):
				raise Refused('%s is no name an extension folder can carry' % ext)
			names = {rel for rel, _, _ in files}
			data = {rel: blob for rel, _, blob in files}
			if 'manifest.json' not in data:
				raise Refused('%s has no manifest.json' % ext)
			m = check_manifest(ext, data['manifest.json'], {n for n in names if n.startswith('scripts/') and n.endswith('.sh')})
			executable = {'scripts/%s.sh' % s for s in m.get('scripts', [])}
			blob = pack(ext, files, when, executable)
			check_archive(blob, ext)
			sha = hashlib.sha256(blob).hexdigest()
			old = entries.get(ext)
			if old and old.get('version') == m['version'] and old.get('sha256', '').lower() != sha:
				raise Refused('the index offers %s %s with other bytes: give it a new version in its manifest' % (ext, m['version']))
			rel = 'archives/%s-%s.tar.gz' % (ext, m['version'])
			target = os.path.join(ROOT, rel)
			if os.path.exists(target):
				with open(target, 'rb') as f:
					if f.read() != blob:
						raise Refused('%s exists with other bytes: give %s a new version in its manifest' % (rel, ext))
			else:
				write_atomic(target, blob)
			url = args.base_url + rel
			if not https_address(url):
				raise Refused('%s is no address the box takes' % url)
			entry = {'id': ext, 'name': m['name'], 'version': m['version'], 'url': url, 'sha256': sha}
			if m.get('description'):
				entry['description'] = m['description']
			entries[ext] = entry
			print('%-16s %-8s %7d bytes  %s' % (ext, m['version'], len(blob), rel))
		text = json.dumps({'format': 1, 'extensions': sorted(entries.values(), key=lambda e: e['id'])},
				  indent='\t', ensure_ascii=False) + '\n'
		if len(text.encode()) > MAX_INDEX:
			raise Refused('the index would be larger than 256 KiB')
		write_atomic(index_path, text.encode())
	except (Refused, OSError, UnicodeError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as e:
		print('build-index: %s' % e, file=sys.stderr)
		return 1
	print('index.json: %d extensions; sign it again: tools/sign-index.sh <private key>' % len(entries))
	return 0


if __name__ == '__main__':
	sys.exit(main())
