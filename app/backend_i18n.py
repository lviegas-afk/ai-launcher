"""Traducciones de los textos que produce el backend (es/en/de/fr/ja/pt).

API (stdlib pura, nunca lanza):
    tr(es, en=None, de=None, fr=None, ja=None, pt=None, **kw)
    tr_in(lang, es, en=None, de=None, fr=None, ja=None, pt=None, **kw)
    current_lang()
    LANGS

Las traducciones se escriben en el sitio de la llamada (no hay registro central).
"""
import locale
import os

LANGS = ("es", "en", "de", "fr", "ja", "pt")

_OS_LANG = None


def _detect_os():
    global _OS_LANG
    if _OS_LANG is None:
        found = "en"
        cands = []
        try:
            cands.append(locale.getdefaultlocale()[0])
        except Exception:
            pass
        cands += [os.environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
        for c in cands:
            p = str(c or "").lower()[:2]
            if p in LANGS:
                found = p
                break
        _OS_LANG = found
    return _OS_LANG


def current_lang():
    """Idioma activo: CONFIG["lang"] (se lee en cada llamada) o, si está vacío/inválido, el del sistema."""
    try:
        import backend
        v = backend.CONFIG.get("lang")
        if v in LANGS:
            return v
    except Exception:
        pass
    try:
        return _detect_os()
    except Exception:
        return "en"


def tr_in(lang, es, en=None, de=None, fr=None, ja=None, pt=None, **kw):
    try:
        opts = {"es": es, "en": en, "de": de, "fr": fr, "ja": ja, "pt": pt}
        s = opts.get(lang) or en or es
        return s.format(**kw) if kw else s
    except Exception:
        return es


def tr(es, en=None, de=None, fr=None, ja=None, pt=None, **kw):
    return tr_in(current_lang(), es, en, de, fr, ja, pt, **kw)
