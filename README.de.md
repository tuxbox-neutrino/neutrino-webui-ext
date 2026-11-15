# neutrino-webui-ext

*English version: [README.md](README.md)*

Der signierte Index der Erweiterungsverwaltung der Neutrino-Web-UI und
die Archive, die er anbietet. Auf der Box: Einstellungen →
**Erweiterungen** → *Installieren* → *Liste laden*.

## Wie eine Box ihn nutzt

Der Browser lädt `index.json` und `index.json.sig` aus diesem Repository
(`https://raw.githubusercontent.com/tuxbox-neutrino/neutrino-webui-ext/HEAD/index.json`,
der Standardwert von `ext_index_url` in `webui.conf`) und lädt beide auf
die Box hoch. Die Box prüft die Signatur mit dem Schlüssel, den ihr Image
mitbringt, `<datadir>/tuxbox/neutrino/webui-index.pub`, und zeigt die
Einträge nur, wenn sie stimmt. Zum Installieren lädt der Browser das
Archiv eines Eintrags ebenfalls von hier, und die Box installiert es nur,
wenn seine SHA-256 die ist, die der signierte Index nennt. Die Box selbst
lädt nichts herunter.

Die Archive liegen in diesem Repository, nicht in Releases: Eine Seite
darf Raw-Dateien eines Repositorys lesen, einen Download aus einem
GitHub-Release aber nicht.

## Aufbau

```
index.json                          der Index (Format 1)
index.json.sig                      Ed25519-Signatur über index.json, base64
webui-index.pub                     der öffentliche Schlüssel der Images, zum Prüfen
archives/<id>-<version>.tar.gz      ein Archiv je Erweiterung und Version
tools/build-index.py                packt Erweiterungen, schreibt index.json
tools/sign-index.sh                 signiert index.json
tools/check-index.py                prüft Schlüssel, Index, Signatur und Archive wie eine Box
tools/indexrules.py                 die Regeln der Box, an die sich beide Werkzeuge halten
```

Was eine Erweiterung und ihr Archiv sein müssen, beschreibt der
Neutrino-Baum, [data/web-ui/extensions/README.de.md](https://github.com/tuxbox-neutrino/neutrino/blob/master/data/web-ui/extensions/README.de.md).

## Eine Erweiterung aufnehmen oder aktualisieren

1. Haben sich ihre Dateien geändert, bekommt die Erweiterung eine neue
   Version in ihrer `manifest.json`: Ein Archiv im Index ändert sich nie,
   und der Index nennt die Version des Manifests darin.
2. Packen und in den Index eintragen. Eine Quelle ist der Ordner mit der
   `manifest.json`, auf der Platte oder als `REPOSITORY:REVISION:PFAD`:
   ```sh
   tools/build-index.py ~/sources/neutrino:master:data/web-ui/extensions/files
   ```
   `--remove <id>` nimmt eine Erweiterung aus dem Index.
3. Den Index signieren und das Ergebnis prüfen:
   ```sh
   tools/sign-index.sh <privater Schlüssel>
   ```
4. `index.json`, `index.json.sig` und das neue Archiv zusammen committen.

Dieselben Dateien ergeben dasselbe Archiv: Einträge sortiert, Eigentümer
0, die Rechte 0644 und 0755 (0755 für eine ausführbare Datei und ein
Skript, das das Manifest nennt), als Zeit die des letzten Commits, der
den Ordner geändert hat (bei einem Ordner auf der Platte
`SOURCE_DATE_EPOCH`, sonst die Zeit seiner jüngsten Datei), gzip ohne
Namen und Zeit. Jedes Archiv wird vor dem Schreiben mit den Regeln
geprüft, nach denen die Box installiert, und ein Archiv einer Version,
die der Index oder `archives/` schon mit anderen Bytes hält, wird
abgelehnt. Die Werkzeuge lassen nichts durch, was die Box ablehnt, und
in einem Punkt sind sie strenger als die Box: JSON mit einem maskierten
Surrogat (`\ud800` und ähnliche) lehnen sie ab, weil die Box solche
Escapes auf ihre eigene Weise liest. `.gitattributes` hält Git davon ab,
die Bytes von `index.json` zu ändern, über die die Signatur geht. Der
private Schlüssel gehört in kein Repository; ihn behält, wer den Index
signiert.

## Lizenz

GPL-2.0, siehe [LICENSE](LICENSE). Jede Erweiterung bringt ihre eigene
Lizenzdatei mit.
