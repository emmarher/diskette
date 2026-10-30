#!/usr/bin/env python3
"""
diskette — Memoria compartida multi-agente (MCP server, cero dependencias)
=======================================================================
Un solo archivo. SQLite + FTS5 como índice local; el espejo Markdown es la
FUENTE CANÓNICA (legible por humanos).

Uso:
    python3 diskette.py            # MCP stdio (sincroniza MD -> SQLite al arrancar)
    python3 diskette.py --sync     # sincronizar MD -> SQLite y reportar
    python3 diskette.py --export   # regenerar MD limpio desde SQLite
    python3 diskette.py --path     # imprimir ruta de la DB
    python3 diskette.py --optimize # optimizar índice FTS5

Variables de entorno:
    DISKETTE_DB    Ruta de la base SQLite (default: ~/.diskette/diskette.db)
    DISKETTE_MD    Dir. del espejo Markdown canónico (default: ~/.diskette)
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
    os.path.join(os.path.expanduser("~"), ".diskette", "memory.db"),
)
MD_DIR = os.environ.get(
    "DISKETTE_MD",
    os.path.join(os.path.expanduser("~"), ".diskette"),
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

_ENTRY_RE = re.compile(r"^## \[([a-z]+)\] (.+?)[ \t]*\r?$", re.M)
_META_RE = re.compile(
    r"^\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?:[ ]·[ ]([^*`]+?))?(?:[ ]`([^`]*)`)?\*[ \t]*$"
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


# ------------------------------------------------------------ markdown ---

def md_mirror(row):
    """Append-only espejo humano; al ser parseable, ES la fuente canónica."""
    os.makedirs(MD_DIR, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in row["project"])
    path = os.path.join(MD_DIR, f"{safe}.md")
    tags = f" `{row['tags']}`" if row["tags"] else ""
    agent = f" · {row['agent']}" if row["agent"] else ""
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(
            f"\n## [{row['kind']}] {row['title']}\n"
            f"*{time.strftime('%Y-%m-%d %H:%M', time.localtime(row['created_at']))}"
            f"{agent}{tags}*\n\n{row['content'].strip()}\n"
        )


def sync_from_md():
    """Importar entradas nuevas del espejo Markdown a SQLite (idempotente)."""
    if not os.path.isdir(MD_DIR):
        return 0
    added = 0
    MAX_FILES = 1000  # Límite para prevenir DoS
    MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB por archivo (cross-platform safe)
    file_count = 0
    real_md_dir = os.path.realpath(MD_DIR)
    for fn in sorted(os.listdir(MD_DIR)):
        if file_count >= MAX_FILES:
            break
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        project = fn[:-3]
        try:
            file_path = os.path.join(MD_DIR, fn)
            # Protección contra symlinks que apunten fuera del directorio
            real_path = os.path.realpath(file_path)
            if not real_path.startswith(real_md_dir + os.sep) and real_path != real_md_dir:
                continue
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                continue
            with open(file_path, encoding="utf-8") as f:
                text = f.read().replace("\r\n", "\n")
        except OSError:
            continue
        file_count += 1
        matches = list(_ENTRY_RE.finditer(text))
        for i, m in enumerate(matches):
            kind, title = m.group(1), m.group(2).strip()
            if kind not in KINDS:
                kind = "fact"
            block = text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
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
                continue
            key = entry_key(project, title, content)
            cur = db().execute(
                "INSERT OR IGNORE INTO memories (key, project, kind, title, content,"
                " tags, agent, session, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (key, project, kind, title, content, tags, agent, "", created),
            )
            added += cur.rowcount
    db().commit()
    return added


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
    md_mirror(row)
    return f"OK guardado #{row['id']} [{kind}] en '{project}' (proyecta a MD)"


def t_search(args):
    q = args.get("query", "").strip()
    project = args.get("project")
    try:
        limit = min(int(args.get("limit", 8)), 25)
    except (ValueError, TypeError):
        limit = 8
    if not q:
        return "(query vacío)"
    # Validación más robusta: solo permitir caracteres alfanuméricos y espacios
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
        "\n> Protocolo: guarda con mem_save al completar trabajo significativo; "
        "busca con mem_search antes de decidir; deja un 'progress' al terminar."
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
    return (f"OK eliminado #{mid}. Nota: el espejo MD es append-only; "
            "regenera con --export y (si usas git) commitea para propagar el borrado.")


TOOLS = [
    {
        "name": "mem_save",
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
                "agent": {"type": "string", "description": "Quien escribe (opencode, claude-code, cursor, etc.)"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "mem_search",
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
        "name": "mem_session_start",
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
        "name": "mem_recent",
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
        "name": "mem_forget",
        "description": "Eliminar una memoria por id (regenera MD con --export si quieres limpiar el espejo).",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]

DISPATCH = {
    "mem_save": t_save,
    "mem_search": t_search,
    "mem_recent": t_recent,
    "mem_session_start": t_session_start,
    "mem_forget": t_forget,
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
    added = sync_from_md()
    if added:
        sys.stderr.write(f"[diskette] {added} entrada(s) importada(s) de {MD_DIR}\n")
    else:
        sys.stderr.write(f"[diskette] listo (MD: {MD_DIR})\n")
    db()
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
                "serverInfo": {"name": "diskette", "version": "0.31.0"},
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


def run_export():
    db()
    os.makedirs(MD_DIR, exist_ok=True)
    projects = [r[0] for r in db().execute("SELECT DISTINCT project FROM memories ORDER BY project")]
    for p in projects:
        rows = db().execute(
            "SELECT * FROM memories WHERE project=? ORDER BY created_at", (p,)
        ).fetchall()
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in p)
        with open(os.path.join(MD_DIR, f"{safe}.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# Memoria: {p}\n")
            for r in rows:
                tags = f" `{r['tags']}`" if r['tags'] else ""
                agent = f" · {r['agent']}" if r['agent'] else ""
                f.write(
                    f"\n## [{r['kind']}] {r['title']}\n"
                    f"*{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['created_at']))}"
                    f"{agent}{tags}*\n\n{r['content'].strip()}\n"
                )
    print(f"exportado {len(projects)} proyecto(s) a {MD_DIR}")


if __name__ == "__main__":
    if "--export" in sys.argv:
        run_export()
    elif "--sync" in sys.argv:
        n = sync_from_md()
        print(f"{n} entrada(s) nueva(s) importadas a {DB_PATH}")
    elif "--path" in sys.argv:
        print(DB_PATH)
    elif "--optimize" in sys.argv:
        db().execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
        db().commit()
        print("FTS optimizado")
    else:
        run_stdio()
