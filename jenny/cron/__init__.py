"""Cron service for scheduled agent tasks.

Volutamente vuoto: v. la nota in ``jenny/apps/__init__.py``. Qui c'era in più una
``__getattr__`` di modulo che rendeva pigro ``CronService``, e costava più di
quanto rendesse: una ``__getattr__`` fa diventare ``Any`` **ogni** attributo
sconosciuto del package, cioè ``jenny.cron.CronServiceTypo`` smetteva di essere
un errore — su un package che sta nel sottoinsieme *bloccante* di pyright
(v. ``jenny/session/manager.py``, che quel prezzo lo cita per non pagarlo).
In cambio evitava un import che, misurato, non faceva nessuno: le 117
importazioni del package nominano tutte il sottomodulo.
"""
