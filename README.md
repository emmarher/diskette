# diskette — Memoria local multi-agente (MCP, cero dependencias)

Un solo archivo Python (`diskette.py`), sin paquetes externos.
Funciona en **Windows, macOS y Linux** (solo stdlib de Python 3.8+).

## Arquitectura

- **Fuente canónica** = Markdown (`~/.diskette/*.md`)
- **Índice local** = SQLite + FTS5 (`~/.diskette/memory.db`)

La memoria es **local a la máquina**. Todos los agentes de la misma máquina la comparten.

### ¿Cómo se almacena cada proyecto?

**Un archivo Markdown por proyecto + una sola base de datos para todos:**

```
~/.diskette/
├── memory.db          ← TODOS los proyectos (tabla única, columna `project`)
├── api-gateway.md     ← proyecto "api-gateway"
├── web-frontend.md    ← proyecto "web-frontend"
└── cli-tool.md        ← proyecto "cli-tool"
```

- Cada `mem_save` con `project="api-gateway"` hace append en `api-gateway.md`
  y una fila en `memory.db` con `project='api-gateway'`.
- La búsqueda (`mem_search`) es **cross-proyecto por defecto**; el parámetro
  `project` es un filtro opcional.
- `mem_session_start` y `mem_recent` filtran por el proyecto actual
  (carpeta de trabajo) salvo que indiques otro.

## Instalación

```bash
git clone https://github.com/emmarher/diskette.git ~/tools/diskette
cd ~/tools/diskette
python3 diskette.py --path   # verifica que resuelve la ruta de la DB
```

No hace falta instalar nada más.

## Configuración del MCP

### OpenCode (`~/.config/opencode/opencode.json` o `opencode.json` del proyecto)

```json
{
  "mcp": {
    "diskette": {
      "type": "local",
      "command": ["python3", "/home/TU_USUARIO/tools/diskette/diskette.py"],
      "enabled": true
    }
  }
}
```

> **Windows**: cambia `python3` por `python` y la ruta a
> `C:/Users/TU_USUARIO/tools/diskette/diskette.py`.

### Claude Code

```bash
claude mcp add --scope user diskette -- python3 ~/tools/diskette/diskette.py
```

### Cursor (`~/.cursor/mcp.json` o `.cursor/mcp.json` del proyecto)

```json
{
  "mcpServers": {
    "diskette": {
      "command": "python3",
      "args": ["/home/TU_USUARIO/tools/diskette/diskette.py"]
    }
  }
}
```

### Antigravity / CommandCode

Registra el servidor MCP stdio en tu configuración de agentes:
command `python3`, arg la ruta a `diskette.py`. Mismo protocolo en tus
reglas globales.

## Protocolo obligatorio (AGENTS.md / CLAUDE.md)

Copia el contenido de `AGENTS.md` en las reglas de tu agente:

```
## Memoria compartida (diskette)
- AL INICIAR sesión o tras compaction: mem_session_start.
- ANTES de decidir arquitectura, reabrir un bug o repetir un análisis: mem_search.
- AL TERMINAR trabajo significativo: mem_save (kind: decision|debug|progress|
  pattern|fact|lesson). Pon agent="<tu agente>" y tags.
- Lo que no guardas se pierde para los demás agentes.
```

> **Importante**: conectar el MCP solo expone las herramientas. Sin este
> bloque en el system prompt, el agente no las llamará de forma consistente.

## Herramientas

| Tool | Uso |
|---|---|
| `mem_save` | Guardar decision/debug/progress/pattern/fact/lesson |
| `mem_search` | FTS5 + BM25 con prefijo (`jwt auth` → `jwt* AND auth*`), fallback LIKE |
| `mem_session_start` | Recap al iniciar sesión o tras compaction |
| `mem_recent` | Lista cronológica (filtrable por proyecto) |
| `mem_forget` | Borrar por id (el MD es append-only; usa `--export` para limpiar) |

## Comandos útiles

```bash
python3 diskette.py --sync      # forzar importación MD → SQLite
python3 diskette.py --export    # regenerar los .md limpios desde SQLite
python3 diskette.py --optimize  # optimizar índice FTS5
python3 diskette.py --path      # ver ruta de la base de datos
```

## Variables de entorno (opcionales)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DISKETTE_MD` | `~/.diskette` | Directorio de los archivos Markdown |
| `DISKETTE_DB` | `~/.diskette/memory.db` | Ruta de la base SQLite |

## Notas

- **Sync automático**: al arrancar, el MCP importa entradas nuevas del MD
  (idempotente por hash de contenido). `git pull` + abrir agente = sincronizado.
- **Seguridad**: validación de inputs, escape de LIKE, límite de 1MB por `.md`,
  sin symlinks fuera del directorio, WAL mode para concurrencia.
- **Borrados**: `mem_forget` borra de SQLite; para propagar el borrado al MD
  corre `--export` y commitea.
