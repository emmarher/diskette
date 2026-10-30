## Memoria local (diskette)

- AL INICIAR sesión o tras compaction: llama **siempre** a `diskette_session_start`.
- ANTES de decidir arquitectura, reabrir un bug, elegir librería o repetir un análisis: `diskette_search`.
- AL TERMINAR trabajo significativo (decisión, fix, patrón, progreso): `diskette_save`
  - kind: decision | debug | progress | pattern | fact | lesson
  - Pon siempre `agent="<tu-agente>"` (opencode / claude-code / cursor / antigravity) y tags útiles.
- Al dejar trabajo a medias o cerrar sesión: `diskette_save` con kind=`progress`.

La memoria es **local a esta máquina** (`~/.diskette`).  
Lo que no guardas se pierde entre sesiones y entre agentes de esta misma máquina.