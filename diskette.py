#!/usr/bin/env python3
"""
diskette — Memoria compartida multi-agente (MCP server, cero dependencias)
=======================================================================
Un solo archivo. SQLite + FTS5 como backend; DB-only (sin espejo MD automático).

Uso:
    python3 diskette.py              # MCP stdio
    python3 diskette.py --dump [p]   # volcar proyecto(s) a diskette-<proyecto>.md
    python3 diskette.py --path       # imprimir ruta de la DB
    python3 diskette.py --optimize   # optimizar índice FTS5

Variables de entorno:
    DISKETTE_DB    Ruta de la base SQLite (default: ~/.diskette/diskette.db)
"""

import atexit
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

DB_PATH = os.environ.get(
    "DISKETTE_DB",
    os.path.join(os.path.expanduser("~"), ".diskette", "diskette.db"),
)

KINDS = (
    "decision",   # decisiones de arquitectura, tradeoffs, alternativas rechazadas
    "debug",      # causa raíz, fix aplicado, archivos tocados
    "progress",   # dónde quedó el trabajo, qué sigue
    "pattern",    # convenciones, patrones de código reutilizables
    "fact",       # hechos del proyecto: config, setup, comandos
    "lesson",     # aprendizajes, gotchas, errores que no repetir
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL DEFAULT '',
    project    TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'fact',
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    agent      TEXT NOT NULL DEFAULT '',
    session    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mem_key ON memories(key) WHERE key != '';
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title, content, tags,
    content='memories', content_rowid='id',
    tokenize='porter unicode61',
    prefix='2 3'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
    VALUES ('delete', old.id, old.title, old.content, old.tags);
    INSERT INTO memories_fts(rowid, title, content, tags)
    VALUES (new.id, new.title, new.content, new.tags);
END;
CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project, created_at DESC);
"""

# Regex para parsear archivos diskette-*.md (dump format) al importar
_ENTRY_RE = re.compile(r"^## \[([a-z]+)\] (.+?)[ \t]*\r?$", re.M)
_META_RE = re.compile(
    r"^\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?:[ ]·[ ]([^*`]+?))?(?:[ ]`([^`]*)`)?\\*[ \t]*$"
)

_conn = None


def _close_db():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


atexit.register(_close_db)


def db():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA cache_size=-8000")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(_SCHEMA)
    return _conn


def now():
    return time.time()


def entry_key(project, title, content):
    raw = f"{project}|{title.strip()}|{content.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------- dump / load ---

MAX_DUMP_FILE = 1 * 1024 * 1024  # 1 MB


def _safe_filename(name):
    """Nombre de archivo seguro cross-platform (Win/Mac/Linux)."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


def _render_md(project, rows):
    """Genera el contenido Markdown para un volcado de proyecto."""
    parts = [f"# Memoria: {project}\n"]
    for r in rows:
        tags = f" `{r['tags']}`" if r["tags"] else ""
        agent = f" · {r['agent']}" if r["agent"] else ""
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        parts.append(
            f"\n## [{r['kind']}] {r['title']}\n"
            f"*{ts}{agent}{tags}*\n\n{r['content'].strip()}\n"
        )
    return "".join(parts)


def dump_project(project, out_dir=None):
    """Volcar memorias de un proyecto a diskette-<proyecto>.md.

    Args:
        project: Nombre del proyecto.
        out_dir: Directorio destino (default: directorio actual).

    Returns:
        Tupla (ruta_archivo, num_memorias) o (None, 0) si no hay datos.
    """
    rows = db().execute(
        "SELECT * FROM memories WHERE project=? ORDER BY created_at", (project,)
    ).fetchall()
    if not rows:
        return None, 0
    out_dir = out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    filename = f"diskette-{_safe_filename(project)}.md"
    path = os.path.join(out_dir, filename)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_render_md(project, rows))
    return path, len(rows)


def load_from_md(filepath, project=None):
    """Importar entradas desde un archivo Markdown a la DB (idempotente).

    El archivo debe seguir el formato diskette (## [kind] Title / *meta* / content).
    Si no se indica proyecto, se infiere del nombre del archivo.

    Args:
        filepath: Ruta absoluta o relativa al archivo .md.
        project: Nombre del proyecto (default: nombre del archivo sin extensión
                 y sin prefijo 'diskette-').

    Returns:
        Tupla (added, skipped) con contadores.
    """
    filepath = os.path.abspath(filepath)
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"No existe: {filepath}")
    file_size = os.path.getsize(filepath)
    if file_size > MAX_DUMP_FILE:
        raise ValueError(f"Archivo demasiado grande ({file_size} bytes, máx {MAX_DUMP_FILE})")

    if project is None:
        base = os.path.splitext(os.path.basename(filepath))[0]
        # Quitar prefijo 'diskette-' si existe
        if base.startswith("diskette-"):
            base = base[len("diskette-"):]
        project = base

    with open(filepath, encoding="utf-8") as f:
        text = f.read().replace("\r\n", "\n")

    matches = list(_ENTRY_RE.finditer(text))
    added, skipped = 0, 0
    for i, m in enumerate(matches):
        kind, title = m.group(1), m.group(2).strip()
        if kind not in KINDS:
            kind = "fact"
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end():end]
        lines = block.lstrip("\n").split("\n")
        agent, tags = "", ""
        created = now()
        if lines:
            mm = _META_RE.match(lines[0].strip())
            if mm:
                try:
                    created = time.mktime(time.strptime(mm.group(1), "%Y-%m-%d %H:%M"))
                except (ValueError, OverflowError):
                    pass
                agent = (mm.group(2) or "").strip()
                tags = (mm.group(3) or "").strip()
                lines = lines[1:]
        content = "\n".join(lines).strip("\n ")
        if not title or not content:
            skipped += 1
            continue
        key = entry_key(project, title, content)
        cur = db().execute(
            "INSERT OR IGNORE INTO memories (key, project, kind, title, content,"
            " tags, agent, session, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, project, kind, title, content, tags, agent, "", created),
        )
        if cur.rowcount:
            added += 1
        else:
            skipped += 1
    db().commit()
    return added, skipped


# ---------------------------------------------------------------- tools ---

MAX_TITLE = 500
MAX_CONTENT = 50_000  # ~50 KB


def _escape_like(s: str) -> str:
    """Escapa metacaracteres LIKE para evitar patrones wildcard inyectados."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def t_save(args):
    project = args.get("project") or os.path.basename(os.getcwd())
    kind = args.get("kind", "fact")
    if kind not in KINDS:
        kind = "fact"
    title = args.get("title", "").strip()[:MAX_TITLE]
    content = args.get("content", "").strip()[:MAX_CONTENT]
    if not title or not content:
        return "(título y contenido requeridos)"
    cur = db().execute(
        "INSERT INTO memories (key, project, kind, title, content, tags, agent, session, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            entry_key(project, title, content), project, kind, title, content,
            args.get("tags", "").strip(),
            args.get("agent", "").strip(),
            args.get("session", "").strip(),
            now(),
        ),
    )
    db().commit()
    row = db().execute("SELECT * FROM memories WHERE id=?", (cur.lastrowid,)).fetchone()
    if row is None:
        return "Error: no se pudo recuperar la memoria guardada"
    return f"OK guardado #{row['id']} [{kind}] en '{project}'"


def t_search(args):
    q = args.get("query", "").strip()
    project = args.get("project")
    try:
        limit = min(int(args.get("limit", 8)), 25)
    except (ValueError, TypeError):
        limit = 8
    if not q:
        return "(query vacío)"
    # Validación: solo permitir caracteres alfanuméricos y espacios
    safe_terms = [t for t in q.split() if t.isalnum()]
    if not safe_terms:
        return "(query inválido: caracteres no permitidos)"
    terms = " AND ".join(t + "*" for t in safe_terms)
    sql = (
        "SELECT m.*, bm25(memories_fts, 6.0, 1.0, 3.0) AS rank"
        " FROM memories_fts f JOIN memories m ON m.id = f.rowid"
        " WHERE memories_fts MATCH ?"
    )
    params = [terms]
    if project:
        sql += " AND m.project = ?"
        params.append(project)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = db().execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        like = f"%{_escape_like(q)}%"
        sql2 = "SELECT * FROM memories WHERE (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')"
        p2 = [like, like]
        if project:
            sql2 += " AND project = ?"
            p2.append(project)
        sql2 += " ORDER BY created_at DESC LIMIT ?"
        p2.append(limit)
        rows = db().execute(sql2, p2).fetchall()
    if not rows:
        return "(sin resultados)"
    return "\n\n".join(
        f"[#{r['id']} | {r['kind']} | {r['project']}]\n**{r['title']}**\n{r['content']}"
        for r in rows
    )


def t_recent(args):
    project = args.get("project")
    try:
        limit = min(int(args.get("limit", 10)), 50)
    except (ValueError, TypeError):
        limit = 10
    sql = "SELECT * FROM memories"
    params = []
    if project:
        sql += " WHERE project = ?"
        params.append(project)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db().execute(sql, params).fetchall()
    if not rows:
        return "(sin memorias aún)"
    return "\n".join(
        f"- #{r['id']} [{r['kind']}] {r['title']} — {r['project']} "
        f"({time.strftime('%Y-%m-%d', time.localtime(r['created_at']))})"
        for r in rows
    )


def t_session_start(args):
    """Recap para el inicio de sesión: progreso reciente + pendientes."""
    project = args.get("project") or os.path.basename(os.getcwd())
    parts = [f"## Proyecto: {project}", "### Actividad reciente"]
    parts.append(t_recent({"project": project, "limit": 10}))
    prog = db().execute(
        "SELECT * FROM memories WHERE project=? AND kind='progress'"
        " ORDER BY created_at DESC LIMIT 5",
        (project,),
    ).fetchall()
    if prog:
        parts.append("### Últimos estados de progreso")
        parts.extend(f"- {r['title']}: {r['content'][:300]}" for r in prog)
    parts.append(
        "\n> Protocolo: guarda con diskette_save al completar trabajo significativo; "
        "busca con diskette_search antes de decidir; deja un 'progress' al terminar."
    )
    return "\n".join(parts)


def t_forget(args):
    try:
        mid = int(args.get("id", 0))
    except (ValueError, TypeError):
        return "(id inválido: debe ser un número entero)"
    if not mid:
        return "(id requerido)"
    db().execute("DELETE FROM memories WHERE id=?", (mid,))
    db().commit()
    return f"OK eliminado #{mid}"


def t_dump(args):
    """Volcar memorias de un proyecto a diskette-<proyecto>.md en el dir actual."""
    project = args.get("project") or os.path.basename(os.getcwd())
    out_dir = args.get("out_dir") or os.getcwd()
    path, count = dump_project(project, out_dir)
    if not path:
        return f"(sin memorias para proyecto '{project}')"
    return f"OK volcado {count} memoria(s) de '{project}' → {path}"


def t_load(args):
    """Importar memorias desde un archivo Markdown al proyecto en la DB."""
    filepath = args.get("filepath", "").strip()
    if not filepath:
        return "(filepath requerido)"
    project = args.get("project")  # None => inferir del nombre del archivo
    try:
        added, skipped = load_from_md(filepath, project)
    except FileNotFoundError as e:
        return f"(error: {e})"
    except ValueError as e:
        return f"(error: {e})"
    parts = [f"OK importadas {added} memoria(s)"]
    if skipped:
        parts.append(f"({skipped} omitidas: duplicadas o vacías)")
    if project:
        parts.append(f"en proyecto '{project}'")
    return " ".join(parts)


TOOLS = [
    {
        "name": "diskette_save",
        "description": (
            "Guardar una memoria persistente en la base local (la leen TODOS "
            "los agentes de esta máquina). Usar después de decisiones, bugfixes, "
            "descubrimientos, cambios de config o patrones. "
            "NO es opcional al terminar trabajo significativo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título corto y específico"},
                "content": {"type": "string", "description": "Detalle: qué, por qué, archivos, comandos"},
                "kind": {"type": "string", "enum": list(KINDS), "default": "fact"},
                "project": {"type": "string", "description": "Default: carpeta actual"},
                "tags": {"type": "string", "description": "Comma-separated para descubribilidad"},
                "agent": {"type": "string", "description": "Quien escribe (opencode, claude-code, cursor, antigravity, etc.)"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "diskette_search",
        "description": (
            "Buscar en la memoria local (FTS5, ranking BM25). Llamar ANTES de "
            "tomar decisiones que puedan haberse resuelto antes. Si no hay "
            "resultados, reintentar con términos distintos."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string"},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "diskette_session_start",
        "description": (
            "LLAMAR SIEMPRE al iniciar sesión: resume actividad reciente del proyecto, "
            "estado de progreso y reglas. Recupera contexto tras compaction."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
        },
    },
    {
        "name": "diskette_recent",
        "description": "Listar memorias más recientes (cronológico).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "diskette_forget",
        "description": "Eliminar una memoria por id.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "diskette_dump",
        "description": (
            "Volcar memorias de un proyecto a un archivo diskette-<proyecto>.md. "
            "Útil para compartir, hacer backup, o commitear a git."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Proyecto a volcar (default: carpeta actual)"},
                "out_dir": {"type": "string", "description": "Directorio destino (default: directorio actual)"},
            },
        },
    },
    {
        "name": "diskette_load",
        "description": (
            "Importar memorias desde un archivo Markdown (formato diskette) a la DB. "
            "El usuario indica la ruta del archivo. Idempotente: no duplica entradas."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Ruta al archivo .md a importar"},
                "project": {
                    "type": "string",
                    "description": "Proyecto destino (default: se infiere del nombre del archivo sin prefijo diskette-)",
                },
            },
            "required": ["filepath"],
        },
    },
]

DISPATCH = {
    "diskette_save": t_save,
    "diskette_search": t_search,
    "diskette_recent": t_recent,
    "diskette_session_start": t_session_start,
    "diskette_forget": t_forget,
    "diskette_dump": t_dump,
    "diskette_load": t_load,
}

# ------------------------------------------------------------- MCP stdio ---

def respond(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def fail(msg_id, code, message):
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}) + "\n"
    )
    sys.stdout.flush()


def run_stdio():
    db()
    sys.stderr.write(f"[diskette] listo (DB: {DB_PATH})\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if method == "initialize":
            respond(msg_id, {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "diskette", "version": "0.4.0"},
            })
        elif method == "ping":
            respond(msg_id, {})
        elif method == "tools/list":
            respond(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            name, args = params.get("name"), params.get("arguments") or {}
            fn = DISPATCH.get(name)
            if not fn:
                fail(msg_id, -32602, f"herramienta desconocida: {name}")
                continue
            try:
                out = fn(args)
                respond(msg_id, {"content": [{"type": "text", "text": out}], "isError": False})
            except Exception as e:
                respond(msg_id, {"content": [{"type": "text", "text": f"(diskette error: {type(e).__name__})"}], "isError": True})
        # notifications/* : ignorar


# ------------------------------------------------------------------ CLI ---

def run_dump_cli(argv):
    """CLI: --dump [proyecto...] — volcar a diskette-<proyecto>.md."""
    db()
    # Recolectar proyectos a volcar
    projects = []
    idx = argv.index("--dump") + 1
    while idx < len(argv) and not argv[idx].startswith("--"):
        projects.append(argv[idx])
        idx += 1
    if not projects:
        # Todos los proyectos
        projects = [r[0] for r in db().execute(
            "SELECT DISTINCT project FROM memories ORDER BY project"
        )]
    if not projects:
        print("(sin memorias en la DB)")
        return
    for p in projects:
        path, count = dump_project(p)
        if path:
            print(f"  {count} memoria(s) → {path}")
        else:
            print(f"  '{p}': sin memorias")


def run_load_cli(argv):
    """CLI: --load <archivo.md> [--project nombre]"""
    idx = argv.index("--load") + 1
    if idx >= len(argv) or argv[idx].startswith("--"):
        print("Uso: diskette.py --load <archivo.md> [--project nombre]")
        sys.exit(1)
    filepath = argv[idx]
    project = None
    if "--project" in argv:
        pi = argv.index("--project") + 1
        if pi < len(argv):
            project = argv[pi]
    try:
        added, skipped = load_from_md(filepath, project)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    print(f"{added} entrada(s) importada(s), {skipped} omitida(s)")


if __name__ == "__main__":
    if "--dump" in sys.argv:
        run_dump_cli(sys.argv)
    elif "--load" in sys.argv:
        run_load_cli(sys.argv)
    elif "--path" in sys.argv:
        print(DB_PATH)
    elif "--optimize" in sys.argv:
        db().execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
        db().commit()
        print("FTS optimizado")
    else:
        run_stdio()
