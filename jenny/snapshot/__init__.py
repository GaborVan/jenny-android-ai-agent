"""Versioning locale del workspace: snapshot content-addressed + backup cifrato.

Il package fornisce:

- ``store``/``engine``: motore di snapshot puro stdlib (sha256 + zlib + json)
  con deduplica dei contenuti, retention e garbage collection;
- ``service``: trigger automatici di sistema (debounce, checkpoint pre-Dream,
  snapshot a shutdown) senza alcun coinvolgimento dell'LLM;
- ``crypto``/``crypto_backends``: container di backup cifrato AES-256-GCM
  (javax.crypto su Android, ``cryptography`` solo in dev/test);
- ``backup``: export/import del backup e staging del ripristino;
- ``restore_marker``: protocollo di swap atomico del workspace al boot.
"""
