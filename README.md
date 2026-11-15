# neutrino-webui-ext

*Deutsche Fassung: [README.de.md](README.de.md)*

The signed index of the Neutrino-Web-UI's extension manager, and the
archives it offers. On the box: Settings → **Extensions** → *Install* →
*Load list*.

## How a box uses it

The browser loads `index.json` and `index.json.sig` from this
repository (`https://raw.githubusercontent.com/tuxbox-neutrino/neutrino-webui-ext/HEAD/index.json`,
the default of `ext_index_url` in `webui.conf`) and uploads both to the
box. The box checks the signature with the key its image carries,
`<datadir>/tuxbox/neutrino/webui-index.pub`, and shows the entries only
when it holds. To install an entry the browser loads its archive from
here as well, and the box installs it only when its SHA-256 is the one
the signed index names. The box itself downloads nothing.

The archives lie in this repository, not in releases: a page may read
raw files of a repository, but not a download from a GitHub release.

## Layout

```
index.json                          the index (format 1)
index.json.sig                      Ed25519 signature over index.json, base64
webui-index.pub                     the public key the images carry, to check against
archives/<id>-<version>.tar.gz      one archive per extension and version
tools/build-index.py                packs extensions, writes index.json
tools/sign-index.sh                 signs index.json
tools/check-index.py                checks key, index, signature and archives as a box would
tools/indexrules.py                 the rules of the box both tools hold to
```

What an extension and its archive have to be is described in the
Neutrino tree, [data/web-ui/extensions/README.md](https://github.com/tuxbox-neutrino/neutrino/blob/master/data/web-ui/extensions/README.md).

## Adding or updating an extension

1. Give the extension a new version in its `manifest.json` when its
   files changed: an archive that is in the index never changes, and the
   index names the version of the manifest inside it.
2. Pack it and enter it in the index. A source is the folder that holds
   `manifest.json`, on disk or as `REPOSITORY:REVISION:PATH`:
   ```sh
   tools/build-index.py ~/sources/neutrino:master:data/web-ui/extensions/files
   ```
   `--remove <id>` takes an extension out of the index.
3. Sign the index and check the result:
   ```sh
   tools/sign-index.sh <private key>
   ```
4. Commit `index.json`, `index.json.sig` and the new archive together.

The same files give the same archive: entries sorted, owner 0, the
modes 0644 and 0755 (0755 for an executable file and a script the
manifest names), as time that of the last commit that changed the folder
(for a folder on disk `SOURCE_DATE_EPOCH`, or else the time of its
newest file), gzip without a name or a time. Every archive is checked
with the rules the box installs by before it is written, and an archive
of a version that the index or `archives/` already holds with other
bytes is refused. The tools never pass what the box refuses, and in one
point they are stricter than the box: JSON with an escaped surrogate
(`\ud800` and the like) is refused, as the box reads such escapes in a
way of its own. `.gitattributes` keeps git from changing the bytes of
`index.json` that the signature is over. The private key never belongs
in a repository; whoever signs the index keeps it.

## License

GPL-2.0, see [LICENSE](LICENSE). Each extension carries its own license
file.
