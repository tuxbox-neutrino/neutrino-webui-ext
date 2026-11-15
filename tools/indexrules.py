# SPDX-License-Identifier: GPL-2.0-or-later
"""What the box takes from the index and from an archive, as
src/coreapi/extensionindex.cpp and src/coreapi/extensions.cpp of Neutrino
check it, and what the page needs of a manifest to show an extension.
build-index.py and check-index.py both hold their output to these rules."""

import gzip
import io
import json
import re
import zlib

MAX_PACKED = 16 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_UNPACKED = 64 * 1024 * 1024
MAX_INDEX = 256 * 1024
MAX_MANIFEST = 256 * 1024
MAX_TEXT = 256
MAX_URL = 2048
MAX_NAME = 100		# a member's whole name in the ustar name field, no prefix
MAX_DEPTH = 64		# the box's JSON readers stop deeper than this

EXTENSION_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
VERSION_RE = re.compile(r'[A-Za-z0-9.+-]{1,32}')
SCRIPT_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,63}')
SHA256_RE = re.compile(r'[0-9a-fA-F]{64}')


class Refused(Exception):
	pass


def format_one(v):
	"""format 1 as the box reads it: a number that is the integer 1 (1.0 too), no boolean."""
	return isinstance(v, (int, float)) and not isinstance(v, bool) and v == 1


def has_control(s):
	return any(ord(c) < 0x20 or ord(c) == 0x7f for c in s)


def extension_name(s):
	return isinstance(s, str) and EXTENSION_RE.fullmatch(s) is not None


def version_word(s):
	return isinstance(s, str) and VERSION_RE.fullmatch(s) is not None


def utf8(s):
	# a lone surrogate the box's reader lets through is three bytes there too
	return s.encode('utf-8', 'surrogatepass')


def plain_text(s):
	return isinstance(s, str) and len(utf8(s)) <= MAX_TEXT and not has_control(s)


def https_address(s):
	if not isinstance(s, str) or not s.startswith('https://'):
		return False
	b = utf8(s)
	return 8 < len(b) <= MAX_URL and all(c > 0x20 and c != 0x7f for c in b)


def js_true(v):
	"""How the page's JavaScript reads a value in a condition."""
	if v is None or v is False:
		return False
	if isinstance(v, (int, float)) and not isinstance(v, bool):
		return v != 0
	if isinstance(v, str):
		return v != ''
	return True


class _Pairs(list):
	"""An object as it was written: every key with its value, twice where given twice."""


def _depth(v):
	"""How deep the box's reader goes for v: every value one level, as its
	stackLimit counts them."""
	if isinstance(v, _Pairs):
		return 1 + max([_depth(x) for _, x in v] or [0])
	if isinstance(v, list):
		return 1 + max([_depth(x) for x in v] or [0])
	return 1


def _no_surrogate_escape(text):
	"""Whether the text escapes no surrogate. The box's reader decodes them in
	its own way (a high one with any escape behind it, a low one alone), so an
	index or a manifest from these tools holds none, and the tools refuse
	them rather than read them differently from the box."""
	i, n, in_string = 0, len(text), False
	while i < n:
		c = text[i]
		if not in_string:
			in_string = c == '"'
			i += 1
			continue
		if c == '"':
			in_string = False
			i += 1
			continue
		if c != '\\':
			i += 1
			continue
		if text[i + 1] == 'u' and 0xd800 <= int(text[i + 2:i + 6], 16) <= 0xdfff:
			return False
		i += 6 if text[i + 1] == 'u' else 2
	return True


def _plain(v, what, duplicates):
	if isinstance(v, _Pairs):
		obj = {}
		for k, x in v:
			if k in obj and not duplicates:
				raise Refused('%s gives "%s" twice' % (what, k))
			obj[k] = _plain(x, what, duplicates)
		return obj
	if isinstance(v, list):
		return [_plain(x, what, duplicates) for x in v]
	return v


def strict_json(data, what, duplicates=False):
	"""JSON as the box's strict reader takes it: one object, every value at
	most 64 deep (the values of a key given twice included), no NaN or
	Infinity, no comment, a key twice only where duplicates is set (the last
	one counts, as for a manifest). Stricter than the box in one point: no
	escaped surrogate at all (_no_surrogate_escape)."""
	def constant(name):
		raise Refused('%s holds %s, which is no JSON' % (what, name))
	try:
		raw = json.loads(data, object_pairs_hook=_Pairs, parse_constant=constant)
		# read only text the parser took: every escape in it is whole
		if not _no_surrogate_escape(data):
			raise Refused('%s escapes a surrogate, which these tools do not publish' % what)
	except ValueError as e:
		raise Refused('%s is no JSON: %s' % (what, e))
	except RecursionError:
		raise Refused('%s is nested deeper than %d' % (what, MAX_DEPTH))
	if not isinstance(raw, _Pairs):
		raise Refused('%s is no JSON object' % what)
	try:
		too_deep = _depth(raw) > MAX_DEPTH
	# deeper than Python's own stack is far deeper than the box takes
	except RecursionError:
		too_deep = True
	if too_deep:
		raise Refused('%s is nested deeper than %d' % (what, MAX_DEPTH))
	return _plain(raw, what, duplicates)


def entry_problem(e):
	"""What is wrong with an entry of the index, as the box reads it; None for nothing."""
	if not isinstance(e, dict):
		return 'is not an object'
	if not extension_name(e.get('id')):
		return 'has no id an extension can carry'
	if not version_word(e.get('version')):
		return 'gives no version of up to 32 letters, digits, dots, pluses and dashes'
	if not https_address(e.get('url')):
		return 'gives no https address the box takes'
	if not isinstance(e.get('sha256'), str) or not SHA256_RE.fullmatch(e['sha256']):
		return 'gives no SHA-256 of 64 hexadecimal digits'
	for k in ('name', 'description'):
		if k in e and not plain_text(e[k]):
			return 'gives a %s that is no text of up to 256 bytes without control characters' % k
	return None


def check_manifest(ext, blob, script_files):
	"""The manifest as the box installs it and the page shows it; script_files
	is the set of regular files below the folder, as scripts/<name>.sh."""
	if len(blob) > MAX_MANIFEST:
		raise Refused('%s: manifest.json is larger than 256 KiB' % ext)
	try:
		text = blob.decode('utf-8-sig')
	except UnicodeDecodeError:
		raise Refused('%s: manifest.json is not UTF-8' % ext)
	m = strict_json(text, ext + '/manifest.json', duplicates=True)
	if not isinstance(m.get('name'), str) or not m['name']:
		raise Refused('%s: the manifest names no "name", and the page shows no extension without one' % ext)
	settings = m.get('settings')
	if not (js_true(m.get('entry')) or js_true(m.get('entry_js')) or
		(js_true(settings) and isinstance(settings, dict) and js_true(settings.get('sections')))):
		raise Refused('%s: the manifest names none of entry, entry_js and settings.sections' % ext)
	if not version_word(m.get('version')):
		raise Refused('%s: the manifest gives no version of up to 32 letters, digits, dots, pluses and dashes' % ext)
	for k in ('name', 'description'):
		if k in m and not plain_text(m[k]):
			raise Refused('%s: "%s" is no text of up to %d bytes without control characters' % (ext, k, MAX_TEXT))
	scripts = m.get('scripts', [])
	if not isinstance(scripts, list) or len(scripts) > 64:
		raise Refused('%s: "scripts" is no list of up to 64 names' % ext)
	for s in scripts:
		if not isinstance(s, str) or not SCRIPT_RE.fullmatch(s):
			raise Refused('%s: script name %r is not one the box takes' % (ext, s))
		if 'scripts/%s.sh' % s not in script_files:
			raise Refused('%s: the manifest names the script %s, but scripts/%s.sh is no file of it' % (ext, s, s))
	return m


def number(field):
	"""A numeric field of a tar header as the box reads it: base 256 behind
	0x80, or octal digits between spaces in front and spaces or NULs behind;
	None where it is neither."""
	if field[0] & 0x80:
		if field[0] != 0x80:
			return None
		out = 0
		for b in field[1:]:
			if out >> 56:
				return None
			out = (out << 8) | b
		return out
	i = 0
	while i < len(field) and field[i] == 0x20:
		i += 1
	j = i
	while j < len(field) and 0x30 <= field[j] <= 0x37:
		j += 1
	if j == i or any(b not in (0x20, 0) for b in field[j:]):
		return None
	return int(field[i:j], 8)


def member_parts(name, directory):
	"""The parts of a member's name, refused where the box refuses them."""
	if directory and name.endswith('/'):
		name = name[:-1]
	if not name or name.startswith('/') or has_control(name):
		return None
	parts = name.split('/')
	if any(p in ('', '.', '..') for p in parts):
		return None
	return parts


def read_archive(blob, ext):
	"""Every member of an archive made like build-index.py makes them, read
	from its own headers: (path, directory, data). Refused where the box would
	refuse the archive or where it is not plain ustar with whole names."""
	if len(blob) > MAX_PACKED:
		raise Refused('%s: the archive is larger than 16 MiB' % ext)
	try:
		with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
			data = gz.read(MAX_UNPACKED + (MAX_MEMBERS + 2) * 1024 + 1)
	except (OSError, EOFError, zlib.error) as e:
		raise Refused('%s: the archive is no gzip file: %s' % (ext, e))
	members = []
	kinds = {}
	total = 0
	off = 0
	while True:
		if off + 512 > len(data):
			raise Refused('%s: the archive ends without its end blocks' % ext)
		h = data[off:off + 512]
		if h == b'\0' * 512:
			break
		size = number(h[124:136])
		check = number(h[148:156])
		mode = number(h[100:108])
		if size is None or check is None or mode is None or check != sum(h[:148]) + 8 * 32 + sum(h[156:]):
			raise Refused('%s: a header of the archive is broken' % ext)
		kind = h[156:157]
		if kind == b'\0':
			kind = b'0'
		if kind == b'5' and size != 0:
			raise Refused('%s: a folder of the archive says it has data' % ext)
		if kind not in (b'0', b'5', b'7'):
			raise Refused('%s: the archive holds an entry of type %r; an extension is files and folders' % (ext, kind))
		if h[257:262] != b'ustar' or h[345:500].strip(b'\0'):
			raise Refused('%s: an entry of the archive is not plain ustar with its whole name in the name field' % ext)
		try:
			name = h[0:100].split(b'\0', 1)[0].decode('utf-8')
		except UnicodeDecodeError:
			raise Refused('%s: the archive holds a name that is not UTF-8' % ext)
		directory = kind == b'5'
		parts = member_parts(name, directory)
		if parts is None:
			raise Refused('%s: the archive holds a name the box refuses: %r' % (ext, name))
		if parts[0] != ext:
			raise Refused('%s: %s is not below %s/' % (ext, name, ext))
		if len(parts) == 1 and not directory:
			raise Refused('%s: the archive holds a file beside its folder: %s' % (ext, name))
		at = ''
		for k, p in enumerate(parts):
			at += ('/' if k else '') + p
			here_dir = directory or k + 1 < len(parts)
			if at in kinds and (kinds[at] != here_dir or (k + 1 == len(parts) and not directory)):
				raise Refused('%s: the archive gives %s twice, or as a file and a folder' % (ext, at))
			kinds[at] = here_dir
		total += size
		if len(members) + 1 > MAX_MEMBERS or total > MAX_UNPACKED:
			raise Refused('%s: the archive holds more than %d names or 64 MiB' % (ext, MAX_MEMBERS))
		start = off + 512
		members.append(('/'.join(parts), directory, data[start:start + size]))
		off = start + ((size + 511) // 512) * 512
	if off + 1024 > len(data) or data[off + 512:off + 1024] != b'\0' * 512:
		raise Refused('%s: the archive ends without its two end blocks' % ext)
	return members


def check_archive(blob, ext):
	"""An archive as the box installs it: its manifest, read from it."""
	members = read_archive(blob, ext)
	files = {p[len(ext) + 1:]: d for p, directory, d in members if not directory}
	if 'manifest.json' not in files:
		raise Refused('%s: the archive has no %s/manifest.json' % (ext, ext))
	scripts = {p for p in files if p.startswith('scripts/') and p.endswith('.sh')}
	return check_manifest(ext, files['manifest.json'], scripts)
