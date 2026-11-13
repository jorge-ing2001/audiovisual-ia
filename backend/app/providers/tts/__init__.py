"""Proveedores TTS (motor 5 — Voz/Audio).

Cada proveedor implementa el ``TTSProvider`` de ``providers.base``. Los defaults
locales/gratuitos (``kokoro`` offline con degradación a ``edge`` online) reutilizan
el código MIT de lemo-opuscar (``core/tts/tts.py`` y ``core/tts/tts_zh.py``) vía
subproceso; ver ``NOTICE`` para la atribución.
"""
