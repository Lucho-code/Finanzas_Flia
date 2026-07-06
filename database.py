import re
import sqlite3
import os
import shutil
import threading
from datetime import datetime, date
from calendar import monthrange
import pytz

TIMEZONE = pytz.timezone("America/Argentina/Buenos_Aires")
DB_PATH  = os.getenv("DB_PATH", "finanzas.db")

# Copia en tiempo real a carpetas sincronizadas a la nube (opcional)
_LIVE_DESTINATIONS = [
    os.path.join(os.path.expanduser("~"), "OneDrive", "FinanzasFamilia", "finanzas_live.db"),
    r"G:\Mi unidad\FinanzasFamilia\finanzas_live.db",
]


def _backup_live():
    def _copy():
        for dest in _LIVE_DESTINATIONS:
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(DB_PATH, dest)
            except Exception:
                pass
    threading.Thread(target=_copy, daemon=True).start()


# Categorías por defecto: (nombre, tipo, presupuesto_mensual, palabras_clave)
CATEGORIAS_DEFAULT = [
    ("Comida",          "gasto", None, "super,supermercado,comida,almuerzo,cena,restaurant,restaurante,"
                                        "verduleria,verduleria,carniceria,kiosco,pizza,delivery,panaderia"),
    ("Transporte",       "gasto", None, "nafta,combustible,colectivo,subte,uber,taxi,peaje,estacionamiento,"
                                        "auto,mecanico,service"),
    ("Servicios",        "gasto", None, "luz,gas,agua,internet,telefono,celular,cable,expensas,wifi"),
    ("Salud",            "gasto", None, "farmacia,medico,dentista,obra social,remedios,psicologo"),
    ("Hogar",            "gasto", None, "alquiler,muebles,limpieza,ferreteria,decoracion,arreglo"),
    ("Entretenimiento",  "gasto", None, "cine,streaming,netflix,spotify,salida,bar,boliche,juego"),
    ("Educación",        "gasto", None, "colegio,cuota,libros,utiles,curso,universidad"),
    ("Ropa",             "gasto", None, "ropa,zapatillas,indumentaria,calzado"),
    ("Otros gastos",     "gasto", None, ""),
    ("Sueldo",           "ingreso", None, "sueldo,salario,quincena"),
    ("Extra",            "ingreso", None, "aguinaldo,bono,freelance,changa,venta"),
    ("Aporte al fondo",  "ingreso", None, "aporte,aporté,aporto,aportacion,aportación,fondo comun,fondo común"),
    ("Otros ingresos",   "ingreso", None, ""),
]


class Database:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()
        self._seed_categorias()
        _backup_live()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS members (
                telegram_id   INTEGER PRIMARY KEY,
                name          TEXT NOT NULL,
                role          TEXT DEFAULT 'miembro',
                registered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS categories (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT NOT NULL UNIQUE,
                type           TEXT NOT NULL,
                budget_monthly REAL DEFAULT NULL,
                keywords       TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                type        TEXT NOT NULL,
                category_id INTEGER,
                amount      REAL NOT NULL,
                description TEXT DEFAULT '',
                date        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (telegram_id) REFERENCES members(telegram_id),
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );
        """)
        self.conn.commit()

    def _seed_categorias(self):
        """INSERT OR IGNORE por nombre: agrega categorías nuevas de CATEGORIAS_DEFAULT
        sin duplicar ni tocar las que el usuario ya tiene creadas o editadas."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO categories (name, type, budget_monthly, keywords) VALUES (?, ?, ?, ?)",
            CATEGORIAS_DEFAULT,
        )
        self.conn.commit()

    # ---------- members ----------

    def register_member(self, telegram_id: int, name: str, role: str = "miembro"):
        if self.get_member(telegram_id):
            self.conn.execute("UPDATE members SET name = ? WHERE telegram_id = ?", (name, telegram_id))
        else:
            self.conn.execute(
                "INSERT INTO members (telegram_id, name, role, registered_at) VALUES (?, ?, ?, ?)",
                (telegram_id, name, role, datetime.now(TIMEZONE).isoformat()),
            )
        self.conn.commit()
        _backup_live()

    def get_member(self, telegram_id: int):
        row = self.conn.execute("SELECT * FROM members WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return dict(row) if row else None

    def list_members(self):
        rows = self.conn.execute("SELECT * FROM members ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def find_member_by_name(self, fragment: str):
        fragment = fragment.lower()
        rows = self.conn.execute("SELECT * FROM members").fetchall()
        return [dict(r) for r in rows if fragment in r["name"].lower()]

    def set_role(self, telegram_id: int, role: str):
        self.conn.execute("UPDATE members SET role = ? WHERE telegram_id = ?", (role, telegram_id))
        self.conn.commit()

    # ---------- categories ----------

    def list_categories(self, tipo: str = None):
        if tipo:
            rows = self.conn.execute(
                "SELECT * FROM categories WHERE type = ? ORDER BY name", (tipo,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM categories ORDER BY type, name").fetchall()
        return [dict(r) for r in rows]

    def get_category(self, category_id: int):
        row = self.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        return dict(row) if row else None

    def find_category_by_name(self, fragment: str, tipo: str = None):
        fragment = fragment.lower()
        cats = self.list_categories(tipo)
        return [c for c in cats if fragment in c["name"].lower()]

    def create_category(self, name: str, tipo: str, budget_monthly: float = None, keywords: str = ""):
        self.conn.execute(
            "INSERT OR REPLACE INTO categories (id, name, type, budget_monthly, keywords) "
            "VALUES ((SELECT id FROM categories WHERE name = ?), ?, ?, ?, ?)",
            (name, name, tipo, budget_monthly, keywords),
        )
        self.conn.commit()

    def set_budget(self, category_id: int, budget_monthly: float):
        self.conn.execute(
            "UPDATE categories SET budget_monthly = ? WHERE id = ?", (budget_monthly, category_id)
        )
        self.conn.commit()

    PROTECTED_CATEGORIES = ("Otros gastos", "Otros ingresos")

    def delete_category(self, category_id: int) -> bool:
        """Elimina una categoría. Los movimientos que ya la usaban quedan como
        'Sin categoría' (no se borran). Protege las categorías de fallback
        ('Otros gastos'/'Otros ingresos') porque el resto del sistema las
        necesita como destino por defecto."""
        cat = self.get_category(category_id)
        if not cat or cat["name"] in self.PROTECTED_CATEGORIES:
            return False
        cur = self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def detect_category(self, texto: str, tipo: str):
        """Busca la categoría cuyas keywords coincidan con el texto (por palabra completa,
        para que 'gas' no matchee dentro de 'gasté' o 'gasto'). None si no hay match."""
        texto = texto.lower()
        for cat in self.list_categories(tipo):
            palabras = [p.strip() for p in (cat["keywords"] or "").split(",") if p.strip()]
            if any(re.search(rf"\b{re.escape(p)}\b", texto) for p in palabras):
                return cat
        return None

    def default_category(self, tipo: str):
        nombre = "Otros gastos" if tipo == "gasto" else "Otros ingresos"
        rows = self.conn.execute("SELECT * FROM categories WHERE name = ?", (nombre,)).fetchone()
        return dict(rows) if rows else None

    # ---------- transactions ----------

    def add_transaction(self, telegram_id: int, tipo: str, category_id: int,
                         amount: float, description: str, ts: datetime) -> int:
        cur = self.conn.execute(
            """INSERT INTO transactions (telegram_id, type, category_id, amount, description, date, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, tipo, category_id, amount, description,
             ts.date().isoformat(), ts.isoformat()),
        )
        self.conn.commit()
        _backup_live()
        return cur.lastrowid

    def delete_transaction(self, transaction_id: int, telegram_id: int = None) -> bool:
        if telegram_id is not None:
            cur = self.conn.execute(
                "DELETE FROM transactions WHERE id = ? AND telegram_id = ?",
                (transaction_id, telegram_id),
            )
        else:
            cur = self.conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def update_transaction(self, transaction_id: int, **fields) -> bool:
        if not fields:
            return False
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [transaction_id]
        cur = self.conn.execute(f"UPDATE transactions SET {cols} WHERE id = ?", values)
        self.conn.commit()
        return cur.rowcount > 0

    def get_transaction(self, transaction_id: int):
        row = self.conn.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        return dict(row) if row else None

    def get_transactions_by_period(self, start: date, end: date, telegram_id: int = None) -> list:
        q = """SELECT t.*, m.name member_name, c.name category_name, c.type category_type
               FROM transactions t
               JOIN members m ON t.telegram_id = m.telegram_id
               LEFT JOIN categories c ON t.category_id = c.id
               WHERE t.date BETWEEN ? AND ?"""
        params = [start.isoformat(), end.isoformat()]
        if telegram_id is not None:
            q += " AND t.telegram_id = ?"
            params.append(telegram_id)
        q += " ORDER BY t.date ASC, t.created_at ASC"
        rows = self.conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def get_member_transactions(self, telegram_id: int, limit: int = 10):
        rows = self.conn.execute("""
            SELECT t.*, c.name category_name FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.telegram_id = ?
            ORDER BY t.created_at DESC LIMIT ?
        """, (telegram_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_balance(self, start: date, end: date, telegram_id: int = None) -> dict:
        """Devuelve {'ingresos': x, 'gastos': y, 'saldo': x-y}."""
        recs = self.get_transactions_by_period(start, end, telegram_id)
        ingresos = sum(r["amount"] for r in recs if r["type"] == "ingreso")
        gastos   = sum(r["amount"] for r in recs if r["type"] == "gasto")
        return {"ingresos": ingresos, "gastos": gastos, "saldo": ingresos - gastos}

    def get_spent_by_category(self, start: date, end: date, tipo: str = "gasto") -> list:
        """Devuelve [{'category_name':..., 'total': ...}] ordenado desc."""
        rows = self.conn.execute("""
            SELECT c.id category_id, c.name category_name, c.budget_monthly,
                   COALESCE(SUM(t.amount), 0) total
            FROM categories c
            LEFT JOIN transactions t
                   ON t.category_id = c.id AND t.date BETWEEN ? AND ? AND t.type = ?
            WHERE c.type = ?
            GROUP BY c.id
            ORDER BY total DESC
        """, (start.isoformat(), end.isoformat(), tipo, tipo)).fetchall()
        return [dict(r) for r in rows]

    def month_bounds(self, d: date) -> tuple:
        last = monthrange(d.year, d.month)[1]
        return d.replace(day=1), d.replace(day=last)
