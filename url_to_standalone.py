#!/usr/bin/env python3
"""
URL to Standalone HTML Converter

Lädt eine Webseite und erstellt eine vollständig selbstständige HTML-Datei
die offline funktioniert - alle Ressourcen (CSS, Bilder, Fonts) werden eingebettet.

Usage:
    python url_to_standalone.py <url> [output.html] [--assets-mode embed|download|hotlink]

Beispiele:
    python url_to_standalone.py https://example.com
    python url_to_standalone.py https://example.com --assets-mode download
    python url_to_standalone.py https://example.com --assets-mode hotlink

Requires:
    pip install playwright requests
    playwright install chromium
"""

import argparse
import asyncio
import base64
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# Wasserzeichen CSS + HTML
WATERMARK_HTML = """
<style>
.preview-watermark {{
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 12px 20px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    z-index: 999999;
    display: flex;
    justify-content: space-between;
    align-items: center;
    box-shadow: 0 2px 10px rgba(0,0,0,0.2);
}}
.preview-watermark strong {{ font-size: 16px; }}
.preview-watermark .preview-meta {{ font-size: 12px; opacity: 0.9; }}
.preview-watermark-corner {{
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: rgba(102, 126, 234, 0.9);
    color: white;
    padding: 8px 16px;
    border-radius: 20px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 12px;
    z-index: 999999;
    box-shadow: 0 2px 10px rgba(0,0,0,0.2);
}}
body {{ padding-top: 50px !important; }}
/* Scrollen erzwingen - überschreibt overflow:hidden von Modals/Cookie-Bannern */
html, body {{ overflow: auto !important; overflow-x: hidden !important; height: auto !important; max-height: none !important; position: static !important; }}
</style>
<div class="preview-watermark">
    <div><strong>🔍 PREVIEW</strong> - Nur zur Ansicht, nicht final</div>
    <div class="preview-meta">Erstellt: {timestamp} | {project_name}</div>
</div>
<div class="preview-watermark-corner">⚠️ Vorschau</div>
"""


def fetch_resource(url: str, timeout: int = 15) -> tuple:
    """Lädt eine externe Ressource herunter."""
    if not REQUESTS_AVAILABLE:
        return (url, None, "requests nicht installiert")
    try:
        response = requests.get(url, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }, verify=True)
        response.raise_for_status()
        return (url, response.content, response.headers.get('Content-Type', ''))
    except Exception as e:
        return (url, None, str(e))


def get_mime_type(url: str, content_type: str = '') -> str:
    """Ermittelt MIME-Type basierend auf URL oder Content-Type Header."""
    url_lower = url.lower()
    content_type_lower = content_type.lower() if content_type else ''
    
    if 'css' in content_type_lower or url_lower.endswith('.css'):
        return 'text/css'
    if 'javascript' in content_type_lower or url_lower.endswith('.js'):
        return 'application/javascript'
    if 'png' in content_type_lower or url_lower.endswith('.png'):
        return 'image/png'
    if 'jpeg' in content_type_lower or '.jpg' in url_lower or '.jpeg' in url_lower:
        return 'image/jpeg'
    if 'gif' in content_type_lower or url_lower.endswith('.gif'):
        return 'image/gif'
    if 'svg' in content_type_lower or url_lower.endswith('.svg'):
        return 'image/svg+xml'
    if 'webp' in content_type_lower or url_lower.endswith('.webp'):
        return 'image/webp'
    if 'ico' in content_type_lower or url_lower.endswith('.ico'):
        return 'image/x-icon'
    if 'woff2' in content_type_lower or url_lower.endswith('.woff2'):
        return 'font/woff2'
    if 'woff' in content_type_lower or url_lower.endswith('.woff'):
        return 'font/woff'
    if 'ttf' in content_type_lower or url_lower.endswith('.ttf'):
        return 'font/ttf'
    if 'eot' in content_type_lower or url_lower.endswith('.eot'):
        return 'application/vnd.ms-fontobject'
    return content_type or 'application/octet-stream'


def inline_css_resources(css_content: str, base_url: str) -> str:
    """Findet url() Referenzen in CSS und bettet sie als Base64 ein."""
    # Pattern für url() - aber vorsichtig mit CSS-Escapes wie \e oder \f
    url_pattern = r'url\(\s*["\']?([^"\'()\s\\]+(?:\\.[^"\'()\s\\]*)*)["\']?\s*\)'
    
    def replace_url(match):
        try:
            url = match.group(1)
            if not url or url.startswith('data:'):
                return match.group(0)
            
            # CSS-Escapes entfernen für URL-Auflösung
            clean_url = url.replace('\\', '')
            
            absolute_url = urljoin(base_url, clean_url)
            _, content, content_type = fetch_resource(absolute_url)
            
            if content:
                mime = get_mime_type(clean_url, content_type)
                b64 = base64.b64encode(content).decode('utf-8')
                return f'url("data:{mime};base64,{b64}")'
            
            return match.group(0)
        except Exception:
            return match.group(0)
    
    try:
        return re.sub(url_pattern, replace_url, css_content)
    except Exception:
        # Falls Regex fehlschlägt, Original zurückgeben
        return css_content


def save_asset_to_file(content: bytes, url: str, assets_folder: str, asset_type: str = 'misc') -> str:
    """Speichert Asset in Ordner und gibt relativen Pfad zurück."""
    import hashlib
    # Dateiname aus URL-Hash + Extension generieren
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    parsed = urlparse(url)
    path_parts = parsed.path.split('/')
    original_name = path_parts[-1] if path_parts[-1] else 'asset'
    # Extension ermitteln
    ext = ''
    if '.' in original_name:
        ext = '.' + original_name.split('.')[-1].split('?')[0][:10]
    filename = f"{url_hash}{ext}"

    # Unterordner nach Typ
    subdir = Path(assets_folder) / asset_type
    subdir.mkdir(exist_ok=True)

    filepath = subdir / filename
    filepath.write_bytes(content)
    return f"{assets_folder}/{asset_type}/{filename}"


def create_standalone_html(
    html: str,
    base_url: str,
    project_name: str = "Preview",
    include_watermark: bool = False,
    remove_scripts: bool = True,
    max_workers: int = 10,
    assets_mode: str = 'embed',
    assets_folder: Optional[str] = None
) -> Dict[str, Any]:
    """
    Konvertiert HTML zu einer selbstständigen Datei mit eingebetteten Ressourcen.

    Args:
        assets_mode: 'embed' (Base64), 'download' (Ordner), 'hotlink' (Original-URLs)
        assets_folder: Ordnername für download-Modus
    """
    stats = {
        'stylesheets_inlined': 0,
        'images_inlined': 0,
        'fonts_inlined': 0,
        'assets_downloaded': 0,
        'total_size_before': len(html),
        'resources_failed': 0,
        'source_url': base_url
    }
    errors = []
    
    # 1. Scripts entfernen (optional)
    if remove_scripts:
        html = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', html, flags=re.IGNORECASE | re.DOTALL)
        html = re.sub(r'\s+on\w+="[^"]*"', '', html, flags=re.IGNORECASE)
        html = re.sub(r"\s+on\w+='[^']*'", '', html, flags=re.IGNORECASE)

    # 2. Lazy-Load Attribute konvertieren (data-src -> src, data-srcset -> srcset)
    lazy_attrs = ['data-src', 'data-lazy-src', 'data-original', 'data-lazy']
    for attr in lazy_attrs:
        # Für Bilder ohne src oder mit Placeholder-src
        html = re.sub(
            rf'(<img[^>]*?)(?:\s+src=["\'][^"\']*["\'])?\s+{attr}=["\']([^"\']+)["\']([^>]*>)',
            r'\1 src="\2"\3',
            html,
            flags=re.IGNORECASE
        )

    # data-srcset -> srcset
    html = re.sub(
        r'(<(?:img|source)[^>]*?)\s+data-srcset=["\']([^"\']+)["\']',
        r'\1 srcset="\2"',
        html,
        flags=re.IGNORECASE
    )

    # loading="lazy" entfernen (nicht mehr nötig für Standalone)
    html = re.sub(r'\s+loading=["\']lazy["\']', '', html, flags=re.IGNORECASE)

    # 3. Externe Stylesheets verarbeiten
    if assets_mode != 'hotlink':
        stylesheet_patterns = [
            r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']([^"\']+)["\'][^>]*>',
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']stylesheet["\'][^>]*>',
            r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\'][^>]*>'
        ]

        stylesheet_matches = set()
        for pattern in stylesheet_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            stylesheet_matches.update(matches)

        # Stylesheets parallel laden
        if stylesheet_matches and REQUESTS_AVAILABLE:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for href in stylesheet_matches:
                    absolute_url = urljoin(base_url, href)
                    futures[executor.submit(fetch_resource, absolute_url)] = href

                for future in as_completed(futures):
                    href = futures[future]
                    url, content, content_type = future.result()

                    if content:
                        try:
                            # CSS dekodieren - verschiedene Encodings versuchen
                            css_content = None
                            for encoding in ['utf-8', 'latin-1', 'cp1252']:
                                try:
                                    css_content = content.decode(encoding)
                                    break
                                except (UnicodeDecodeError, LookupError):
                                    continue

                            if css_content is None:
                                css_content = content.decode('utf-8', errors='replace')

                            href_escaped = re.escape(href.split("?")[0])
                            link_pattern = rf'<link[^>]+href=["\'][^"\']*{href_escaped}[^"\']*["\'][^>]*>'

                            if assets_mode == 'embed':
                                # CSS-interne Ressourcen einbetten
                                css_content = inline_css_resources(css_content, url)
                                replacement = f'<style>/* Inlined: {href} */\n{css_content}</style>'
                                html = re.sub(link_pattern, lambda m: replacement, html, count=1, flags=re.IGNORECASE)
                                stats['stylesheets_inlined'] += 1
                            elif assets_mode == 'download' and assets_folder:
                                # CSS in Datei speichern
                                local_path = save_asset_to_file(content, url, assets_folder, 'css')
                                html = re.sub(link_pattern, f'<link rel="stylesheet" href="{local_path}">', html, count=1, flags=re.IGNORECASE)
                                stats['assets_downloaded'] += 1

                        except Exception as e:
                            errors.append(f"CSS Parse-Fehler: {href} - {str(e)}")
                            stats['resources_failed'] += 1
                    else:
                        errors.append(f"CSS nicht geladen: {href} - {content_type}")
                        stats['resources_failed'] += 1
    
    # 4. Bilder verarbeiten (<img src>)
    if assets_mode != 'hotlink':
        img_pattern = r'(<img[^>]+src=)(["\'])([^"\']+)(\2[^>]*>)'

        def replace_image(match):
            prefix = match.group(1)
            quote = match.group(2)
            src = match.group(3)
            suffix = match.group(4)

            if src.startswith('data:'):
                return match.group(0)

            absolute_url = urljoin(base_url, src)
            _, content, content_type = fetch_resource(absolute_url)

            if content:
                if assets_mode == 'embed':
                    mime = get_mime_type(src, content_type)
                    b64 = base64.b64encode(content).decode('utf-8')
                    data_url = f'data:{mime};base64,{b64}'
                    stats['images_inlined'] += 1
                    return f'{prefix}{quote}{data_url}{suffix}'
                elif assets_mode == 'download' and assets_folder:
                    local_path = save_asset_to_file(content, absolute_url, assets_folder, 'images')
                    stats['assets_downloaded'] += 1
                    return f'{prefix}{quote}{local_path}{suffix}'
            else:
                errors.append(f"Bild nicht geladen: {src}")
                stats['resources_failed'] += 1
            return match.group(0)

        if REQUESTS_AVAILABLE:
            html = re.sub(img_pattern, replace_image, html, flags=re.IGNORECASE)
    
    # 5. srcset Bilder verarbeiten
    if assets_mode != 'hotlink':
        srcset_pattern = r'srcset=["\']([^"\']+)["\']'

        def replace_srcset(match):
            srcset = match.group(1)
            new_srcset_parts = []

            for part in srcset.split(','):
                part = part.strip()
                if not part:
                    continue

                pieces = part.split()
                if not pieces:
                    continue

                src = pieces[0]
                descriptor = pieces[1] if len(pieces) > 1 else ''

                if src.startswith('data:'):
                    new_srcset_parts.append(part)
                    continue

                absolute_url = urljoin(base_url, src)
                _, content, content_type = fetch_resource(absolute_url)

                if content:
                    if assets_mode == 'embed':
                        mime = get_mime_type(src, content_type)
                        b64 = base64.b64encode(content).decode('utf-8')
                        data_url = f'data:{mime};base64,{b64}'
                        new_srcset_parts.append(f'{data_url} {descriptor}'.strip())
                        stats['images_inlined'] += 1
                    elif assets_mode == 'download' and assets_folder:
                        local_path = save_asset_to_file(content, absolute_url, assets_folder, 'images')
                        new_srcset_parts.append(f'{local_path} {descriptor}'.strip())
                        stats['assets_downloaded'] += 1
                else:
                    new_srcset_parts.append(part)

            if new_srcset_parts:
                return f'srcset="{", ".join(new_srcset_parts)}"'
            return match.group(0)

        if REQUESTS_AVAILABLE:
            html = re.sub(srcset_pattern, replace_srcset, html, flags=re.IGNORECASE)
    
    # 6. Background-Images in Style-Attributen
    if assets_mode != 'hotlink':
        style_url_pattern = r'(style=["\'][^"\']*url\(["\']?)([^"\')\s]+)(["\']?\)[^"\']*["\'])'

        def replace_style_url(match):
            prefix = match.group(1)
            url = match.group(2)
            suffix = match.group(3)

            if url.startswith('data:'):
                return match.group(0)

            absolute_url = urljoin(base_url, url)
            _, content, content_type = fetch_resource(absolute_url)

            if content:
                if assets_mode == 'embed':
                    mime = get_mime_type(url, content_type)
                    b64 = base64.b64encode(content).decode('utf-8')
                    data_url = f'data:{mime};base64,{b64}'
                    stats['images_inlined'] += 1
                    return f'{prefix}{data_url}{suffix}'
                elif assets_mode == 'download' and assets_folder:
                    local_path = save_asset_to_file(content, absolute_url, assets_folder, 'images')
                    stats['assets_downloaded'] += 1
                    return f'{prefix}{local_path}{suffix}'

            return match.group(0)

        if REQUESTS_AVAILABLE:
            html = re.sub(style_url_pattern, replace_style_url, html, flags=re.IGNORECASE)
    
    # 7. <picture><source> verarbeiten
    if assets_mode != 'hotlink':
        source_pattern = r'(<source[^>]+srcset=)(["\'])([^"\']+)(\2[^>]*>)'

        def replace_source(match):
            prefix = match.group(1)
            quote = match.group(2)
            srcset = match.group(3)
            suffix = match.group(4)

            new_parts = []
            for part in srcset.split(','):
                part = part.strip()
                if not part:
                    continue

                pieces = part.split()
                src = pieces[0]
                descriptor = pieces[1] if len(pieces) > 1 else ''

                if src.startswith('data:'):
                    new_parts.append(part)
                    continue

                absolute_url = urljoin(base_url, src)
                _, content, content_type = fetch_resource(absolute_url)

                if content:
                    if assets_mode == 'embed':
                        mime = get_mime_type(src, content_type)
                        b64 = base64.b64encode(content).decode('utf-8')
                        data_url = f'data:{mime};base64,{b64}'
                        new_parts.append(f'{data_url} {descriptor}'.strip())
                        stats['images_inlined'] += 1
                    elif assets_mode == 'download' and assets_folder:
                        local_path = save_asset_to_file(content, absolute_url, assets_folder, 'images')
                        new_parts.append(f'{local_path} {descriptor}'.strip())
                        stats['assets_downloaded'] += 1
                else:
                    new_parts.append(part)

            if new_parts:
                return f'{prefix}{quote}{", ".join(new_parts)}{suffix}'
            return match.group(0)

        if REQUESTS_AVAILABLE:
            html = re.sub(source_pattern, replace_source, html, flags=re.IGNORECASE)

    # 8. Scroll-Fix einfügen (überschreibt overflow:hidden von Modals/Cookie-Bannern)
    scroll_fix_css = '<style id="standalone-scroll-fix">html, body { overflow: auto !important; overflow-x: hidden !important; height: auto !important; max-height: none !important; position: static !important; }</style>'
    if '</head>' in html.lower():
        html = re.sub(r'(</head>)', scroll_fix_css + r'\1', html, count=1, flags=re.IGNORECASE)
    elif '<body' in html.lower():
        html = re.sub(r'(<body[^>]*>)', r'\1' + scroll_fix_css, html, count=1, flags=re.IGNORECASE)
    else:
        html = scroll_fix_css + html

    # 9. Wasserzeichen einfügen (optional)
    if include_watermark:
        timestamp = datetime.now().strftime('%d.%m.%Y %H:%M')
        watermark = WATERMARK_HTML.format(timestamp=timestamp, project_name=project_name)
        
        if '<body' in html.lower():
            html = re.sub(r'(<body[^>]*>)', r'\1' + watermark, html, count=1, flags=re.IGNORECASE)
        else:
            html = watermark + html
    
    # 10. Meta-Kommentar hinzufügen
    parsed_url = urlparse(base_url)
    domain = parsed_url.netloc[:50] if parsed_url.netloc else base_url[:50]
    
    meta_comment = f"""<!--
╔══════════════════════════════════════════════════════════════════════════╗
║  STANDALONE HTML PREVIEW                                                  ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Projekt:  {project_name:<62} ║
║  Erstellt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<62} ║
║  Quelle:   {domain:<62} ║
╠══════════════════════════════════════════════════════════════════════════╣
║  Diese Datei funktioniert offline und enthält alle eingebetteten         ║
║  Ressourcen. Sie dient nur zur Vorschau - nicht für Produktion.          ║
╚══════════════════════════════════════════════════════════════════════════╝
-->
"""
    if '<!DOCTYPE' in html.upper():
        html = re.sub(r'(<!DOCTYPE[^>]*>)', r'\1\n' + meta_comment, html, count=1, flags=re.IGNORECASE)
    else:
        html = meta_comment + html
    
    stats['total_size_after'] = len(html)
    
    return {
        'html': html,
        'stats': stats,
        'errors': errors
    }


async def fetch_page_html(
    url: str,
    wait_for_network: bool = True,
    close_cookie_banner: bool = True,
    timeout: int = 30000,
    viewport_width: int = 1920,
    viewport_height: int = 1080
) -> Dict[str, Any]:
    """
    Lädt eine Webseite mit Playwright und gibt das HTML zurück.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return {
            'success': False,
            'error': 'Playwright nicht installiert. Bitte "pip install playwright && playwright install chromium" ausführen.'
        }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': viewport_width, 'height': viewport_height},
            ignore_https_errors=True,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = await context.new_page()
        
        try:
            # Seite laden
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout)
            
            # Auf Netzwerk warten
            if wait_for_network:
                try:
                    await page.wait_for_load_state('networkidle', timeout=10000)
                except:
                    pass  # Timeout ist OK
            
            # Cookie-Banner schließen
            if close_cookie_banner:
                cookie_selectors = [
                    # Borlabs Cookie
                    '.brlbs-btn-accept-all',
                    '.brlbs-cmpnt-btn-accept-all',
                    '[data-borlabs-cookie-accept]',
                    # Andere gängige Banner
                    '[class*="cookie"] button[class*="accept"]',
                    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
                    '#onetrust-accept-btn-handler',
                    '.cc-accept-all',
                    '.cmplz-accept',
                    '#cookie-accept-all',
                    'button:has-text("Alle akzeptieren")',
                    'button:has-text("Accept all")',
                    'button:has-text("Akzeptieren")',
                    'button:has-text("Alle Cookies akzeptieren")',
                    '[data-testid="cookie-accept-all"]'
                ]

                for selector in cookie_selectors:
                    try:
                        btn = page.locator(selector).first
                        if await btn.is_visible(timeout=500):
                            await btn.click()
                            await page.wait_for_timeout(800)
                            break
                    except:
                        continue

            # Durch die Seite scrollen um Lazy-Loading zu triggern
            scroll_height = await page.evaluate('document.body.scrollHeight')
            viewport_h = viewport_height
            current_pos = 0

            while current_pos < scroll_height:
                current_pos += viewport_h
                await page.evaluate(f'window.scrollTo(0, {current_pos})')
                await page.wait_for_timeout(300)  # Warten auf Lazy-Load
                # Scroll-Höhe kann sich ändern wenn Content nachgeladen wird
                scroll_height = await page.evaluate('document.body.scrollHeight')

            # Zurück nach oben scrollen
            await page.evaluate('window.scrollTo(0, 0)')
            await page.wait_for_timeout(500)

            # Auf Netzwerk warten nach dem Scrollen
            try:
                await page.wait_for_load_state('networkidle', timeout=5000)
            except:
                pass

            # Cookie-Banner aus DOM entfernen (funktionieren ohne JS nicht)
            if close_cookie_banner:
                await page.evaluate('''() => {
                    const selectors = [
                        // Borlabs Cookie
                        '.brlbs-cmpnt-container',
                        '#BorlabsCookieBox',
                        '[class*="borlabs-cookie"]',
                        // Cookiebot
                        '#CybotCookiebotDialog',
                        '#CybotCookiebotDialogBodyUnderlay',
                        // OneTrust
                        '#onetrust-consent-sdk',
                        '#onetrust-banner-sdk',
                        // Complianz
                        '#cmplz-cookiebanner-container',
                        '.cmplz-cookiebanner',
                        // Cookie Notice
                        '#cookie-notice',
                        '#cookie-law-info-bar',
                        // GDPR Cookie Compliance
                        '#moove_gdpr_cookie_modal',
                        '#moove_gdpr_cookie_info_bar',
                        // Generic
                        '[class*="cookie-banner"]',
                        '[class*="cookie-consent"]',
                        '[id*="cookie-banner"]',
                        '[id*="cookie-consent"]'
                    ];
                    selectors.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => el.remove());
                    });
                }''')

            # HTML extrahieren
            html = await page.content()
            final_url = page.url
            
            await browser.close()
            
            return {
                'success': True,
                'html': html,
                'url': final_url
            }
            
        except Exception as e:
            await browser.close()
            return {
                'success': False,
                'error': str(e)
            }


async def url_to_standalone_html(
    url: str,
    output_path: Optional[str] = None,
    project_name: str = "Preview",
    include_watermark: bool = False,
    remove_scripts: bool = True,
    close_cookie_banner: bool = True,
    assets_mode: str = 'embed'
) -> Dict[str, Any]:
    """
    Hauptfunktion: Lädt URL und erstellt Standalone-HTML.

    Args:
        url: Die zu ladende URL
        output_path: Optionaler Ausgabepfad (sonst wird {domain}_{timestamp}.html verwendet)
        project_name: Projektname für Wasserzeichen
        include_watermark: Wasserzeichen einfügen
        remove_scripts: JavaScript entfernen
        close_cookie_banner: Cookie-Banner automatisch schließen
        assets_mode: 'embed' (Base64), 'download' (Ordner), 'hotlink' (Original-URLs)

    Returns:
        Dict mit 'success', 'output_path', 'html', 'stats', 'errors'
    """
    print(f"🌐 Lade Seite: {url}")
    
    # Seite mit Playwright laden
    result = await fetch_page_html(
        url,
        close_cookie_banner=close_cookie_banner
    )
    
    if not result['success']:
        return {
            'success': False,
            'error': result.get('error', 'Unbekannter Fehler beim Laden')
        }
    
    html = result['html']
    final_url = result['url']
    
    print(f"✅ Seite geladen ({len(html)} Bytes)")

    # Output-Pfad generieren falls nicht angegeben
    if not output_path:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('.', '_').replace(':', '_')
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = f"{domain}_{timestamp}_standalone.html"

    # Assets-Ordner für download-Modus
    assets_folder = None
    if assets_mode == 'download':
        assets_folder = Path(output_path).stem + '_assets'
        Path(assets_folder).mkdir(exist_ok=True)
        print(f"📁 Assets-Ordner: {assets_folder}/")

    if assets_mode == 'hotlink':
        print(f"🔗 Hotlink-Modus: URLs bleiben unverändert")
    else:
        print(f"📦 Verarbeite Ressourcen ({assets_mode})...")

    # HTML zu Standalone konvertieren
    standalone_result = create_standalone_html(
        html=html,
        base_url=final_url,
        project_name=project_name,
        include_watermark=include_watermark,
        remove_scripts=remove_scripts,
        assets_mode=assets_mode,
        assets_folder=assets_folder
    )

    # Speichern
    Path(output_path).write_text(standalone_result['html'], encoding='utf-8')
    
    return {
        'success': True,
        'output_path': output_path,
        'html': standalone_result['html'],
        'stats': standalone_result['stats'],
        'errors': standalone_result['errors']
    }


def main():
    parser = argparse.ArgumentParser(
        description="Lädt eine URL und erstellt eine standalone HTML-Datei mit eingebetteten Ressourcen",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python url_to_standalone.py https://example.com
  python url_to_standalone.py https://example.com preview.html
  python url_to_standalone.py https://example.com --assets-mode download
  python url_to_standalone.py https://example.com --assets-mode hotlink
  python url_to_standalone.py https://example.com --watermark --project-name "Kunde X"
        """
    )
    parser.add_argument("url", help="Die zu ladende URL")
    parser.add_argument("output", nargs='?', help="Ausgabe HTML-Datei (optional)")
    parser.add_argument(
        "--project-name", "-p",
        default="Preview",
        help="Projektname für Wasserzeichen (default: Preview)"
    )
    parser.add_argument(
        "--watermark", "-w",
        action="store_true",
        help="Preview-Wasserzeichen einfügen"
    )
    parser.add_argument(
        "--keep-scripts",
        action="store_true",
        help="JavaScript nicht entfernen"
    )
    parser.add_argument(
        "--no-cookie-close",
        action="store_true",
        help="Cookie-Banner nicht automatisch schließen"
    )
    parser.add_argument(
        "--assets-mode", "-a",
        choices=['embed', 'download', 'hotlink'],
        default='embed',
        help="Asset-Behandlung: embed=Base64 einbetten (default), download=in Ordner speichern, hotlink=Original-URLs"
    )

    args = parser.parse_args()
    
    # Abhängigkeiten prüfen
    if not PLAYWRIGHT_AVAILABLE:
        print("❌ Playwright nicht installiert!")
        print("   Installieren mit: pip install playwright && playwright install chromium")
        sys.exit(1)
    
    if not REQUESTS_AVAILABLE:
        print("⚠️  requests nicht installiert - Ressourcen werden nicht eingebettet")
        print("   Installieren mit: pip install requests")
    
    # Ausführen
    result = asyncio.run(url_to_standalone_html(
        url=args.url,
        output_path=args.output,
        project_name=args.project_name,
        include_watermark=args.watermark,
        remove_scripts=not args.keep_scripts,
        close_cookie_banner=not args.no_cookie_close,
        assets_mode=args.assets_mode
    ))
    
    if not result['success']:
        print(f"❌ Fehler: {result.get('error')}")
        sys.exit(1)
    
    # Statistiken ausgeben
    stats = result['stats']
    print(f"\n✅ HTML erstellt: {result['output_path']}")
    if args.assets_mode == 'embed':
        print(f"   📊 Stylesheets eingebettet: {stats['stylesheets_inlined']}")
        print(f"   🖼️  Bilder eingebettet: {stats['images_inlined']}")
    elif args.assets_mode == 'download':
        print(f"   📁 Assets heruntergeladen: {stats['assets_downloaded']}")
    elif args.assets_mode == 'hotlink':
        print(f"   🔗 Assets: Original-URLs beibehalten")
    print(f"   📦 Größe vorher: {stats['total_size_before'] / 1024:.1f} KB")
    print(f"   📦 Größe nachher: {stats['total_size_after'] / 1024:.1f} KB")
    
    if result['errors']:
        print(f"\n⚠️  {len(result['errors'])} Ressourcen nicht geladen:")
        for error in result['errors'][:5]:
            print(f"   - {error}")
        if len(result['errors']) > 5:
            print(f"   ... und {len(result['errors']) - 5} weitere")


if __name__ == "__main__":
    main()
