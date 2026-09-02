"""Dove vive il manifest delle release: una costante e nient'altro.

Modulo minuscolo e volutamente **senza dipendenze**. L'URL del manifest serve a
due layer che non devono conoscersi: l'updater (``runtime/update_check.py``, che
lo interroga) e lo schema di configurazione (``config/schema.py``, che lo usa
come default di ``updates.manifest_url``). Tenerlo nell'updater costringeva lo
schema a importarlo, e con esso ``httpx``, ``runtime.context`` e
``security.network`` — a import-time, dentro un modulo che ``config/bootstrap``
carica **prima dell'event loop**. Su Chaquopy quel costo si paga a ogni avvio,
per una stringa.

Una sola definizione, quindi, in un posto che entrambi possono importare senza
tirarsi dietro nulla.
"""

from __future__ import annotations

# URL del manifest pubblicato insieme alla release. Punta a
# ``releases/latest/download/``: GitHub lo risolve sempre sull'ultima release,
# quindi il client non deve conoscere il numero di versione per trovarla.
# ``scripts/release.py`` pubblica l'asset con questo nome esatto.
#
# Fork GaborVan: il canale di aggiornamento punta al fork dell'utente (gli APK
# sono firmati con il keystore locale, non con quello dell'autore upstream).
DEFAULT_MANIFEST_URL = (
    "https://github.com/GaborVan/jenny-android-ai-agent/releases/latest/download/latest.json"
)
