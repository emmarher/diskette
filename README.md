# diskette — Memoria local multi-agente (MCP, cero dependencias)

Un solo archivo Python (`diskette.py`), sin paquetes externos.  
Funciona en **Windows, macOS y Linux** (solo stdlib de Python 3.8+).

## Arquitectura

- **Fuente canónica** = Markdown (`~/.diskette/*.md`)
- **Índice local** = SQLite + FTS5 (`~/.diskette/memory.db`)

La memoria es **local a la máquina**. Todos los agentes de la misma máquina la comparten.

## Instalación

```bash
git clone https://github.com/emmarher/diskette.git ~/tools/diskette

### No hace falta instalar nada más.

## Configuración del MCP

OpenCode

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

Claude Code

```bash
claude mcp add --scope user diskette -- python3 ~/tools/diskette/diskette.py
```

Cursor

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

**Windows**: usa `python` en lugar de `python3` y rutas con `/` o `\\`.


## Protocolo obligatorio (AGENTS.md / CLAUDE.md)
Copia el contenido de AGENTS.md en las reglas de tu agente.

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
| DISKETTE_MD | ~/.diskette | Directorio de los archivos Markdown |
| DISKETTE_DB | ~/.diskette/memory.db | Ruta de la base SQLite |


