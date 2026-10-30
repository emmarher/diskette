# Memoria local (diskette)

Sigue exactamente el protocolo definido en AGENTS.md:

- Al iniciar sesión → `diskette_session_start`
- Antes de decidir → `diskette_search`
- Al terminar trabajo significativo → `diskette_save`
- Al dejar trabajo a medias o cerrar sesión → `diskette_save` kind=progress