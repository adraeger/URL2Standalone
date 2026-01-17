# URL2Standalone

Konvertiert Webseiten in vollständig selbstständige HTML-Dateien, die offline funktionieren.

## Features

- **Drei Asset-Modi**: `embed` (Base64), `download` (Ordner), `hotlink` (Original-URLs)
- **Lazy-Loading Support**: Scrollt automatisch durch die Seite, konvertiert `data-src` zu `src`
- **Cookie-Banner-Handling**: Schließt und entfernt automatisch gängige Cookie-Banner
- **JavaScript-Entfernung**: Entfernt Scripts für saubere, statische HTML-Dateien
- **Preview-Wasserzeichen**: Optionales Banner für Kundenpreviews

## Installation

```bash
pip install playwright requests
playwright install chromium
```

## Verwendung

```bash
# Standard (Assets als Base64 einbetten)
python url_to_standalone.py https://example.com

# Assets in Ordner herunterladen
python url_to_standalone.py https://example.com --assets-mode download

# Original-URLs beibehalten (Hotlinking)
python url_to_standalone.py https://example.com --assets-mode hotlink

# Mit Preview-Wasserzeichen
python url_to_standalone.py https://example.com --watermark --project-name "Kunde X"

# JavaScript behalten
python url_to_standalone.py https://example.com --keep-scripts
```

## CLI-Optionen

| Option | Kurz | Beschreibung |
|--------|------|--------------|
| `--assets-mode` | `-a` | `embed` (default), `download`, `hotlink` |
| `--watermark` | `-w` | Preview-Wasserzeichen einfügen |
| `--project-name` | `-p` | Projektname für Wasserzeichen |
| `--keep-scripts` | | JavaScript nicht entfernen |
| `--no-cookie-close` | | Cookie-Banner nicht automatisch schließen |

## Asset-Modi

| Modus | HTML-Größe | Offline | Anwendungsfall |
|-------|------------|---------|----------------|
| `embed` | Groß (MB) | ✅ Ja | Einzelne Datei zum Versenden |
| `download` | Klein (KB) | ✅ Ja (mit Ordner) | Lokale Vorschau mit Assets |
| `hotlink` | Klein (KB) | ❌ Nein | Schnelle Vorschau, Assets bleiben auf Server |

## Unterstützte Cookie-Banner

Das Tool erkennt und entfernt automatisch:

- Borlabs Cookie
- Cookiebot
- OneTrust
- Complianz
- Cookie Notice
- GDPR Cookie Compliance

## Beispiel-Output

```
🌐 Lade Seite: https://example.com
✅ Seite geladen (1422608 Bytes)
📁 Assets-Ordner: example_com_assets/
📦 Verarbeite Ressourcen (download)...

✅ HTML erstellt: example_com_standalone.html
   📁 Assets heruntergeladen: 64
   📦 Größe vorher: 1389.3 KB
   📦 Größe nachher: 246.5 KB
```

## Anforderungen

- Python 3.8+
- playwright
- requests

## Author

**Achim Dräger**
Internet Marketing Agentur
a.draeger@internet-marketing-agentur.com
