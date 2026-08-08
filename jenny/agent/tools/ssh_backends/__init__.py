"""Backend SSH: un contratto, due implementazioni.

Su Android il client SSH è **nativo** — jsch via bridge Chaquopy — perché la
strada Python avrebbe richiesto ``cryptography``, che su Chaquopy esiste solo
come wheel 42.0.8 (giugno 2024) e che non si può aggiornare: sarebbe stato un
vicolo cieco di manutenzione, oltre alla prima dipendenza con binding nativi
dell'APK. Con jsch la crittografia si aggiorna con un bump di versione Gradle.

Su desktop/test si usa ``asyncssh``, che è una dipendenza **solo di test** (mai
nei requirements Android, mai importata a livello modulo). Serve a due cose:
far girare l'intera suite SSH sul Mac contro un server SSH vero in-process, e
dare un riferimento di comportamento contro cui misurare il bridge.

È lo stesso schema, per le stesse ragioni, di ``jenny/snapshot/crypto_backends``:
``javax.crypto`` su Android, ``cryptography`` solo per i test.
"""
