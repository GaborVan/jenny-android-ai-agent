"""Backend AES-256-GCM per il container di backup cifrato.

Su Android si usa ``javax.crypto`` via bridge Chaquopy (zero dipendenze
Python); su desktop/test si usa ``cryptography`` (dipendenza solo di test,
mai nei requirements Android). Entrambi producono lo stesso identico formato
standard AES-256-GCM, verificato dagli stessi test vector.
"""
