from fastapi import FastAPI, HTTPException, Query, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import os
import hashlib
import secrets

# ---- DB SETUP: PostgreSQL en producción, SQLite en local ----
DATABASE_URL = os.environ.get("DATABASE_URL")  # Railway lo pone automático

if DATABASE_URL:
    # POSTGRESQL (online)
    import psycopg2
    import psycopg2.extras

    def get_db():
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn

    def fetchall(cursor):
        return [dict(row) for row in cursor.fetchall()]

    def fetchone(cursor):
        row = cursor.fetchone()
        return dict(row) if row else None

    PLACEHOLDER = "%s"
    AUTOINCREMENT = "SERIAL PRIMARY KEY"
    DATETIME_NOW = "NOW()"

else:
    # SQLITE (local)
    import sqlite3

    def get_db():
        conn = sqlite3.connect("fiado.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def fetchall(cursor):
        return [dict(row) for row in cursor.fetchall()]

    def fetchone(cursor):
        row = cursor.fetchone()
        return dict(row) if row else None

    PLACEHOLDER = "?"
    AUTOINCREMENT = "INTEGER PRIMARY KEY AUTOINCREMENT"
    DATETIME_NOW = "datetime('now','localtime')"


def P(n=1):
    """Devuelve n placeholders correctos según la DB"""
    if DATABASE_URL:
        return ", ".join(["%s"] * n)
    return ", ".join(["?"] * n)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    if DATABASE_URL:
        # PostgreSQL
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nombre TEXT NOT NULL,
                creado_en TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS negocios (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                nombre TEXT NOT NULL,
                creado_en TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sesiones (
                token TEXT PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                negocio_id INTEGER,
                creado_en TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contactos (
                id SERIAL PRIMARY KEY,
                negocio_id INTEGER NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL CHECK(tipo IN ('clientes','proveedores')),
                nombre TEXT NOT NULL,
                telefono TEXT DEFAULT '',
                deuda REAL DEFAULT 0,
                ultimo_movimiento TIMESTAMP,
                creado_en TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transacciones (
                id SERIAL PRIMARY KEY,
                contacto_id INTEGER NOT NULL REFERENCES contactos(id) ON DELETE CASCADE,
                tipo TEXT NOT NULL CHECK(tipo IN ('deuda','pago')),
                monto REAL NOT NULL,
                nota TEXT DEFAULT '',
                fecha TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ventas (
                id SERIAL PRIMARY KEY,
                negocio_id INTEGER NOT NULL REFERENCES negocios(id) ON DELETE CASCADE,
                descripcion TEXT NOT NULL,
                monto REAL NOT NULL,
                fecha TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        # SQLite
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                nombre TEXT NOT NULL,
                creado_en TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS negocios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                nombre TEXT NOT NULL,
                creado_en TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sesiones (
                token TEXT PRIMARY KEY,
                usuario_id INTEGER NOT NULL,
                negocio_id INTEGER,
                creado_en TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS contactos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negocio_id INTEGER NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('clientes','proveedores')),
                nombre TEXT NOT NULL,
                telefono TEXT DEFAULT '',
                deuda REAL DEFAULT 0,
                ultimo_movimiento TEXT,
                creado_en TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (negocio_id) REFERENCES negocios(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS transacciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contacto_id INTEGER NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('deuda','pago')),
                monto REAL NOT NULL,
                nota TEXT DEFAULT '',
                fecha TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (contacto_id) REFERENCES contactos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS ventas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                negocio_id INTEGER NOT NULL,
                descripcion TEXT NOT NULL,
                monto REAL NOT NULL,
                fecha TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (negocio_id) REFERENCES negocios(id) ON DELETE CASCADE
            );
        """)

    conn.commit()
    conn.close()

init_db()

# ---- APP ----
app = FastAPI(title="FiadoApp API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---- MODELS ----
class RegisterData(BaseModel):
    email: str
    password: str
    nombre: str
    negocio: str

class LoginData(BaseModel):
    email: str
    password: str

class NegocioCreate(BaseModel):
    nombre: str

class ContactoCreate(BaseModel):
    nombre: str
    telefono: Optional[str] = ""
    deuda_inicial: Optional[float] = 0
    tipo: str

class ContactoUpdate(BaseModel):
    nombre: Optional[str] = None
    telefono: Optional[str] = None
    deuda: Optional[float] = None

class TransaccionCreate(BaseModel):
    tipo: str
    monto: float
    nota: Optional[str] = ""

# ---- HELPERS ----
def hash_password(p): return hashlib.sha256(p.encode()).hexdigest()

def q(sql):
    """Adapta ? a %s para PostgreSQL"""
    if DATABASE_URL:
        return sql.replace("?", "%s")
    return sql

def get_session(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="No autenticado")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("""
        SELECT s.*, u.nombre as user_nombre
        FROM sesiones s JOIN usuarios u ON s.usuario_id=u.id
        WHERE s.token=?
    """), (token,))
    session = fetchone(cur)
    conn.close()
    if not session:
        raise HTTPException(status_code=401, detail="Sesión inválida")
    session["token"] = token
    return session

def get_negocio_id(session):
    nid = session.get("negocio_id")
    if not nid:
        raise HTTPException(status_code=400, detail="Seleccioná un negocio primero")
    return nid

# ---- AUTH ----

@app.post("/auth/register", status_code=201)
def register(data: RegisterData, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT id FROM usuarios WHERE email=?"), (data.email,))
    if fetchone(cur):
        conn.close()
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    cur.execute(q("INSERT INTO usuarios (email, password_hash, nombre) VALUES (?,?,?)"),
                (data.email, hash_password(data.password), data.nombre))

    if DATABASE_URL:
        cur.execute("SELECT lastval()")
        user_id = cur.fetchone()[0]
    else:
        user_id = cur.lastrowid

    cur.execute(q("INSERT INTO negocios (usuario_id, nombre) VALUES (?,?)"),
                (user_id, data.negocio))

    if DATABASE_URL:
        cur.execute("SELECT lastval()")
        negocio_id = cur.fetchone()[0]
    else:
        negocio_id = cur.lastrowid

    token = secrets.token_urlsafe(32)
    cur.execute(q("INSERT INTO sesiones (token, usuario_id, negocio_id) VALUES (?,?,?)"),
                (token, user_id, negocio_id))
    conn.commit()
    conn.close()

    response.set_cookie("session_token", token, httponly=True, max_age=86400*30)
    return {"token": token, "negocio_id": negocio_id, "negocio_nombre": data.negocio, "user_nombre": data.nombre}

@app.post("/auth/login")
def login(data: LoginData, response: Response):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM usuarios WHERE email=? AND password_hash=?"),
                (data.email, hash_password(data.password)))
    user = fetchone(cur)
    if not user:
        conn.close()
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")

    cur.execute(q("SELECT * FROM negocios WHERE usuario_id=? ORDER BY id LIMIT 1"), (user["id"],))
    negocio = fetchone(cur)

    token = secrets.token_urlsafe(32)
    cur.execute(q("INSERT INTO sesiones (token, usuario_id, negocio_id) VALUES (?,?,?)"),
                (token, user["id"], negocio["id"] if negocio else None))
    conn.commit()
    conn.close()

    response.set_cookie("session_token", token, httponly=True, max_age=86400*30)
    return {
        "token": token,
        "negocio_id": negocio["id"] if negocio else None,
        "negocio_nombre": negocio["nombre"] if negocio else "",
        "user_nombre": user["nombre"]
    }

@app.post("/auth/logout")
def logout(response: Response, session=Depends(get_session)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("DELETE FROM sesiones WHERE usuario_id=?"), (session["usuario_id"],))
    conn.commit()
    conn.close()
    response.delete_cookie("session_token")
    return {"ok": True}

@app.get("/auth/me")
def me(session=Depends(get_session)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM negocios WHERE usuario_id=? ORDER BY id"), (session["usuario_id"],))
    negocios = fetchall(cur)
    conn.close()
    return {"user_nombre": session["user_nombre"], "negocio_id": session["negocio_id"], "negocios": negocios}

# ---- NEGOCIOS ----

@app.get("/negocios")
def get_negocios(session=Depends(get_session)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM negocios WHERE usuario_id=? ORDER BY id"), (session["usuario_id"],))
    rows = fetchall(cur)
    conn.close()
    return rows

@app.post("/negocios", status_code=201)
def crear_negocio(data: NegocioCreate, session=Depends(get_session)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("INSERT INTO negocios (usuario_id, nombre) VALUES (?,?)"),
                (session["usuario_id"], data.nombre))
    if DATABASE_URL:
        cur.execute("SELECT lastval()")
        nid = cur.fetchone()[0]
    else:
        nid = cur.lastrowid
    conn.commit()
    cur.execute(q("SELECT * FROM negocios WHERE id=?"), (nid,))
    n = fetchone(cur)
    conn.close()
    return n

@app.put("/negocios/{negocio_id}/seleccionar")
def seleccionar_negocio(negocio_id: int, request: Request, session=Depends(get_session)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM negocios WHERE id=? AND usuario_id=?"),
                (negocio_id, session["usuario_id"]))
    n = fetchone(cur)
    if not n:
        raise HTTPException(status_code=404, detail="Negocio no encontrado")
    token = session["token"]
    cur.execute(q("UPDATE sesiones SET negocio_id=? WHERE token=?"), (negocio_id, token))
    conn.commit()
    conn.close()
    return {"ok": True, "negocio_id": negocio_id, "negocio_nombre": n["nombre"]}

# ---- CONTACTOS ----

@app.get("/contactos")
def get_contactos(tipo: str = Query("clientes"), buscar: Optional[str] = None, session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    if buscar:
        cur.execute(q("""
            SELECT * FROM contactos
            WHERE negocio_id=? AND tipo=? AND (nombre LIKE ? OR telefono LIKE ?)
            ORDER BY deuda DESC, nombre
        """), (nid, tipo, f"%{buscar}%", f"%{buscar}%"))
    else:
        cur.execute(q("SELECT * FROM contactos WHERE negocio_id=? AND tipo=? ORDER BY deuda DESC, nombre"),
                    (nid, tipo))
    rows = fetchall(cur)
    conn.close()
    return rows

@app.post("/contactos", status_code=201)
def crear_contacto(data: ContactoCreate, session=Depends(get_session)):
    nid = get_negocio_id(session)
    if data.tipo not in ("clientes", "proveedores"):
        raise HTTPException(status_code=400, detail="tipo invalido")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("INSERT INTO contactos (negocio_id, tipo, nombre, telefono, deuda) VALUES (?,?,?,?,?)"),
                (nid, data.tipo, data.nombre, data.telefono, data.deuda_inicial or 0))
    if DATABASE_URL:
        cur.execute("SELECT lastval()")
        cid = cur.fetchone()[0]
    else:
        cid = cur.lastrowid
    if data.deuda_inicial and data.deuda_inicial > 0:
        cur.execute(q("INSERT INTO transacciones (contacto_id, tipo, monto, nota) VALUES (?,?,?,?)"),
                    (cid, "deuda", data.deuda_inicial, "Deuda inicial"))
        cur.execute(q("UPDATE contactos SET ultimo_movimiento=NOW() WHERE id=?") if DATABASE_URL
                    else q("UPDATE contactos SET ultimo_movimiento=datetime('now','localtime') WHERE id=?"),
                    (cid,))
    conn.commit()
    cur.execute(q("SELECT * FROM contactos WHERE id=?"), (cid,))
    c = fetchone(cur)
    conn.close()
    return c

@app.put("/contactos/{cid}")
def editar_contacto(cid: int, data: ContactoUpdate, session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM contactos WHERE id=? AND negocio_id=?"), (cid, nid))
    c = fetchone(cur)
    if not c:
        raise HTTPException(status_code=404, detail="No encontrado")
    nombre  = data.nombre   if data.nombre   is not None else c["nombre"]
    telefono = data.telefono if data.telefono is not None else c["telefono"]
    deuda   = data.deuda    if data.deuda    is not None else c["deuda"]
    cur.execute(q("UPDATE contactos SET nombre=?, telefono=?, deuda=? WHERE id=?"),
                (nombre, telefono, deuda, cid))
    conn.commit()
    cur.execute(q("SELECT * FROM contactos WHERE id=?"), (cid,))
    updated = fetchone(cur)
    conn.close()
    return updated

@app.delete("/contactos/{cid}")
def eliminar_contacto(cid: int, session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM contactos WHERE id=? AND negocio_id=?"), (cid, nid))
    if not fetchone(cur):
        raise HTTPException(status_code=404, detail="No encontrado")
    cur.execute(q("DELETE FROM contactos WHERE id=?"), (cid,))
    conn.commit()
    conn.close()
    return {"ok": True}

# ---- TRANSACCIONES ----

@app.get("/contactos/{cid}/transacciones")
def get_transacciones(cid: int, session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM contactos WHERE id=? AND negocio_id=?"), (cid, nid))
    if not fetchone(cur):
        raise HTTPException(status_code=404, detail="No encontrado")
    cur.execute(q("SELECT * FROM transacciones WHERE contacto_id=? ORDER BY fecha DESC"), (cid,))
    rows = fetchall(cur)
    conn.close()
    return rows

@app.post("/contactos/{cid}/transacciones", status_code=201)
def crear_transaccion(cid: int, data: TransaccionCreate, session=Depends(get_session)):
    nid = get_negocio_id(session)
    if data.tipo not in ("deuda", "pago"):
        raise HTTPException(status_code=400, detail="tipo invalido")
    if data.monto <= 0:
        raise HTTPException(status_code=400, detail="monto debe ser > 0")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM contactos WHERE id=? AND negocio_id=?"), (cid, nid))
    c = fetchone(cur)
    if not c:
        raise HTTPException(status_code=404, detail="No encontrado")
    cur.execute(q("INSERT INTO transacciones (contacto_id, tipo, monto, nota) VALUES (?,?,?,?)"),
                (cid, data.tipo, data.monto, data.nota))
    nueva_deuda = c["deuda"] + data.monto if data.tipo == "deuda" else max(0, c["deuda"] - data.monto)
    ts = "NOW()" if DATABASE_URL else "datetime('now','localtime')"
    cur.execute(f"UPDATE contactos SET deuda=?, ultimo_movimiento={ts} WHERE id=?".replace("?", "%s" if DATABASE_URL else "?"),
                (nueva_deuda, cid))
    conn.commit()
    conn.close()
    return {"ok": True, "nueva_deuda": nueva_deuda}

# ---- RESUMEN ----

@app.get("/resumen")
def get_resumen(tipo: str = Query("clientes"), session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT COALESCE(SUM(deuda),0) as t FROM contactos WHERE negocio_id=? AND tipo=?"), (nid, tipo))
    total_deuda = fetchone(cur)["t"]
    cur.execute(q("SELECT COUNT(*) as n FROM contactos WHERE negocio_id=? AND tipo=?"), (nid, tipo))
    total = fetchone(cur)["n"]
    cur.execute(q("""
        SELECT COALESCE(SUM(t.monto),0) as t FROM transacciones t
        JOIN contactos c ON t.contacto_id=c.id
        WHERE c.negocio_id=? AND c.tipo=? AND t.tipo='pago'
    """), (nid, tipo))
    total_pagado = fetchone(cur)["t"]
    conn.close()
    return {"tipo": tipo, "total": total, "total_deuda": total_deuda, "total_pagado": total_pagado}

class VentaCreate(BaseModel):
    descripcion: str
    monto: float

# ---- VENTAS ----

def get_periodo_filter(periodo: str):
    """Devuelve el filtro SQL para el período según la DB"""
    if DATABASE_URL:
        if periodo == 'hoy':
            return "AND fecha >= NOW()::date"
        elif periodo == 'semana':
            return "AND fecha >= date_trunc('week', NOW())"
        elif periodo == 'mes':
            return "AND fecha >= date_trunc('month', NOW())"
        return ""
    else:
        if periodo == 'hoy':
            return "AND date(fecha) = date('now','localtime')"
        elif periodo == 'semana':
            return "AND fecha >= date('now','localtime','-6 days')"
        elif periodo == 'mes':
            return "AND strftime('%Y-%m', fecha) = strftime('%Y-%m', 'now','localtime')"
        return ""

@app.get("/ventas")
def get_ventas(periodo: str = Query("mes"), buscar: Optional[str] = None, session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    pf = get_periodo_filter(periodo)
    if buscar:
        cur.execute(q(f"SELECT * FROM ventas WHERE negocio_id=? AND descripcion LIKE ? {pf} ORDER BY fecha DESC"),
                    (nid, f"%{buscar}%"))
    else:
        cur.execute(q(f"SELECT * FROM ventas WHERE negocio_id=? {pf} ORDER BY fecha DESC"), (nid,))
    rows = fetchall(cur)
    conn.close()
    return rows

@app.get("/ventas/resumen")
def get_ventas_resumen(periodo: str = Query("mes"), session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    pf = get_periodo_filter(periodo)
    cur.execute(q(f"SELECT COALESCE(SUM(monto),0) as total, COUNT(*) as cantidad FROM ventas WHERE negocio_id=? {pf}"), (nid,))
    r = fetchone(cur)
    conn.close()
    return {"total": r["total"], "cantidad": r["cantidad"]}

@app.post("/ventas", status_code=201)
def crear_venta(data: VentaCreate, session=Depends(get_session)):
    nid = get_negocio_id(session)
    if data.monto <= 0:
        raise HTTPException(status_code=400, detail="monto debe ser > 0")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("INSERT INTO ventas (negocio_id, descripcion, monto) VALUES (?,?,?)"),
                (nid, data.descripcion, data.monto))
    if DATABASE_URL:
        cur.execute("SELECT lastval()")
        vid = cur.fetchone()[0]
    else:
        vid = cur.lastrowid
    conn.commit()
    cur.execute(q("SELECT * FROM ventas WHERE id=?"), (vid,))
    v = fetchone(cur)
    conn.close()
    return v

@app.delete("/ventas/{vid}")
def eliminar_venta(vid: int, session=Depends(get_session)):
    nid = get_negocio_id(session)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(q("SELECT * FROM ventas WHERE id=? AND negocio_id=?"), (vid, nid))
    if not fetchone(cur):
        raise HTTPException(status_code=404, detail="No encontrado")
    cur.execute(q("DELETE FROM ventas WHERE id=?"), (vid,))
    conn.commit()
    conn.close()
    return {"ok": True}

if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
