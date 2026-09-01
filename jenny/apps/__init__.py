"""Jenny Apps: workspace app folders with typed actions (storage/http).

See ``.agent/jenny-apps.md`` for the design and the ``app-creator`` skill for
the manifest contract.

**Nessun re-export qui, ed è voluto.** Un ``from jenny.apps.executor import
execute_action`` in testa a questo file viene eseguito da *qualunque* import nel
package, foglie comprese: ``import jenny.apps.storage`` — un modulo che di suo
tocca solo config e filesystem — tirava dentro 113 moduli invece di 33, fra cui
tutto ``jenny.agent`` (45) e ``jenny.providers``, per 167 ms contro 59. Su
Chaquopy quel conto si paga a ogni avvio del gateway.

E non lo pagava nessuno per comodità: misurato, gli import via facciata erano
**zero** contro 29 diretti al sottomodulo. Chi serve un nome lo importa da dove
vive.
"""
