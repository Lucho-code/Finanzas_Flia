import os
import re
import shutil
import logging
import logging.handlers
from datetime import datetime, date, timedelta, time as dt_time

import pytz
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackContext,
)

from database import Database
from reports import build_xlsx, send_email, MESES_ES

load_dotenv()

# --- Logging ---
_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finanzas.log")
_handler = logging.handlers.RotatingFileHandler(
    _log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler, logging.StreamHandler()])
logger = logging.getLogger(__name__)

TOKEN       = os.getenv("TELEGRAM_TOKEN")
ADMIN_IDS   = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
FAMILY_NAME = os.getenv("FAMILY_NAME", "la familia")
TIMEZONE    = pytz.timezone("America/Argentina/Buenos_Aires")

db = Database()

AWAITING_NAME = "awaiting_name"

PALABRAS_GASTO = [
    "gasté", "gaste", "gasto de", "gasto en",
    "pagué", "pague", "pago de", "pago en",
    "compré", "compre", "compra de",
    "usé", "use",
]
PALABRAS_INGRESO = [
    "cobré", "cobre", "cobro de",
    "recibí", "recibi", "recibo de",
    "ingreso de", "ingresó", "ingreso",
    "gané", "gane",
    "me pagaron", "me depositaron", "depositaron",
    "entrada de plata",
    "aporté", "aporte", "aporto", "aportación", "aportacion",
]


def ahora() -> datetime:
    return datetime.now(TIMEZONE)


def es_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _extraer_monto(texto: str):
    """Busca el primer número en el texto y lo devuelve como float, o None si no hay."""
    match = re.search(r"\d[\d.,]*", texto)
    if not match:
        return None
    raw = match.group(0)
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        partes = raw.split(",")
        raw = raw.replace(",", ".") if len(partes[-1]) <= 2 else raw.replace(",", "")
    elif "." in raw:
        # Punto como separador de miles (ej: 5.000 -> 5000) si hay varios grupos
        # o si el grupo final tiene 3 dígitos (formato argentino habitual).
        # Si el grupo final tiene 1-2 dígitos, se interpreta como separador decimal.
        partes = raw.split(".")
        if len(partes) > 2 or len(partes[-1]) == 3:
            raw = raw.replace(".", "")
    try:
        valor = float(raw)
        return valor if valor > 0 else None
    except ValueError:
        return None


async def _pedir_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[AWAITING_NAME] = True
    await update.message.reply_text(
        "¡Hola! Para registrarte en las finanzas familiares escribí tu nombre.\n"
        "Solo el nombre de pila, por ejemplo: Juan"
    )


async def _guardar_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get(AWAITING_NAME):
        return False
    raw = update.message.text.strip().split()[0] if update.message.text and update.message.text.strip() else ""
    if not raw:
        await update.message.reply_text("Escribí tu nombre para registrarte.")
        return True
    name = raw.capitalize()
    db.register_member(update.effective_user.id, name)
    context.user_data[AWAITING_NAME] = False
    await update.message.reply_text(
        f"*¡Bienvenido/a, {name}!* Ya estás registrado en {FAMILY_NAME}.\n"
        f"\n"
        f"*Podés escribir o mandar un audio de voz — funcionan igual.*\n"
        f"\n"
        f"━━━ GASTOS ━━━\n"
        f"\"gasté 5000 en supermercado\"\n"
        f"\"pagué 3000 de nafta\"\n"
        f"\n"
        f"━━━ INGRESOS ━━━\n"
        f"\"cobré 200000 de sueldo\"\n"
        f"\"recibí 5000\"\n"
        f"\n"
        f"━━━ COMANDOS ÚTILES ━━━\n"
        f"/saldo   — saldo del mes\n"
        f"/resumen — gastos por categoría\n"
        f"/ayuda   — ver todos los comandos\n",
        parse_mode="Markdown",
    )
    return True


# ---------- registro de movimientos ----------

async def _check_budget(categoria: dict, previo: float, monto: float, ts: datetime, bot):
    if not categoria.get("budget_monthly"):
        return
    limite = categoria["budget_monthly"]
    nuevo_total = previo + monto
    if previo < limite <= nuevo_total:
        texto = (
            f"⚠️ *Presupuesto excedido* — {categoria['name']}\n"
            f"Gastado: ${nuevo_total:,.2f} / ${limite:,.2f}\n"
            f"Mes: {MESES_ES[ts.month]} {ts.year}"
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=texto, parse_mode="Markdown")
            except Exception:
                pass


async def _registrar_movimiento(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 tipo: str, monto: float, texto_original: str,
                                 categoria_nombre: str = None):
    user   = update.effective_user
    member = db.get_member(user.id)
    ts     = ahora()

    categoria = None
    if categoria_nombre:
        matches = db.find_category_by_name(categoria_nombre, tipo)
        categoria = matches[0] if matches else None
    if not categoria:
        categoria = db.detect_category(texto_original, tipo) or db.default_category(tipo)

    previo = 0.0
    if tipo == "gasto" and categoria.get("budget_monthly"):
        start, end = db.month_bounds(ts.date())
        previo = sum(r["amount"] for r in db.get_transactions_by_period(start, end)
                     if r["category_id"] == categoria["id"])

    tid = db.add_transaction(user.id, tipo, categoria["id"], monto, texto_original, ts)

    etiqueta = "Gasto" if tipo == "gasto" else "Ingreso"
    logger.info("%s uid=%s nombre=%r monto=%.2f categoria=%r",
                etiqueta.upper(), user.id, member["name"], monto, categoria["name"])
    await update.message.reply_text(
        f"*{etiqueta} registrado* (#{tid})\n"
        f"Quién:     {member['name']}\n"
        f"Monto:     ${monto:,.2f}\n"
        f"Categoría: {categoria['name']}\n"
        f"Fecha:     {ts.strftime('%d/%m/%Y')}",
        parse_mode="Markdown",
    )

    if tipo == "gasto":
        await _check_budget(categoria, previo, monto, ts, context.bot)


def _match_palabras(lower: str, palabras: list) -> list:
    """Devuelve las palabras que matchean por palabra completa (evita que 'gas' matchee
    dentro de 'gasté'/'gasto'), junto a la posición donde aparecen."""
    encontradas = []
    for p in palabras:
        m = re.search(rf"\b{re.escape(p)}\b", lower)
        if m:
            encontradas.append((p, m.start()))
    return encontradas


async def _procesar_movimiento(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str):
    lower    = texto.lower()
    monto    = _extraer_monto(texto)
    gastos   = _match_palabras(lower, PALABRAS_GASTO)
    ingresos = _match_palabras(lower, PALABRAS_INGRESO)

    if not monto or not (gastos or ingresos):
        await update.message.reply_text(
            f'No te entendí: "{texto}"\n\n'
            "Contame el monto y si fue un gasto o un ingreso. Ejemplos:\n"
            "  gasté 5000 en supermercado\n"
            "  cobré 200000 de sueldo\n"
            "\n"
            "También podés usar:\n"
            "  /gasto 5000 comida supermercado\n"
            "  /ingreso 200000 sueldo"
        )
        return

    if gastos and ingresos:
        pos_g = min(pos for _, pos in gastos)
        pos_i = min(pos for _, pos in ingresos)
        tipo = "gasto" if pos_g <= pos_i else "ingreso"
    else:
        tipo = "gasto" if gastos else "ingreso"

    await _registrar_movimiento(update, context, tipo, monto, texto)


# ---------- handlers ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = db.get_member(update.effective_user.id)
    if member:
        await update.message.reply_text(
            f"Ya estás registrado como *{member['name']}*\n\n"
            "  /saldo    - Saldo del mes\n"
            "  /resumen  - Gastos por categoría\n"
            "  /gasto    - Registrar un gasto\n"
            "  /ingreso  - Registrar un ingreso\n"
            "  /ayuda    - Ver todos los comandos",
            parse_mode="Markdown",
        )
        return
    await _pedir_nombre(update, context)


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Comandos disponibles*\n"
        "\n"
        "Podés escribir o mandar un audio de voz para registrar movimientos:\n"
        "  \"gasté 5000 en supermercado\"\n"
        "  \"cobré 200000 de sueldo\"\n"
        "\n"
        "*Registro manual:*\n"
        "  /gasto monto categoria descripcion\n"
        "  /ingreso monto categoria descripcion\n"
        "\n"
        "*Consultas:*\n"
        "  /saldo — saldo del mes actual\n"
        "  /resumen — gastos por categoría del mes\n"
        "  /categorias — lista las categorías disponibles\n"
        "\n"
        "*Corrección:*\n"
        "  /borrar id — elimina un movimiento propio\n"
        "\n"
        "_Cualquier duda hablá con el administrador de la familia._",
        parse_mode="Markdown",
    )


async def cmd_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _comando_movimiento(update, context, "gasto")


async def cmd_ingreso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _comando_movimiento(update, context, "ingreso")


async def _comando_movimiento(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str):
    user = update.effective_user
    if not db.get_member(user.id):
        await _pedir_nombre(update, context)
        return
    if not context.args:
        ejemplo = "/gasto 5000 comida supermercado" if tipo == "gasto" else "/ingreso 200000 sueldo"
        await update.message.reply_text(
            f"Uso: /{tipo} monto categoria descripcion\nEjemplo: {ejemplo}"
        )
        return

    monto = _extraer_monto(context.args[0])
    if monto is None:
        await update.message.reply_text("No entendí el monto. Ejemplo: /gasto 5000 comida supermercado")
        return

    categoria_nombre = context.args[1] if len(context.args) > 1 else None
    resto = " ".join(context.args[1:])
    descripcion = resto if resto else f"{tipo} {context.args[0]}"

    await _registrar_movimiento(update, context, tipo, monto, descripcion, categoria_nombre)


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.get_member(user.id):
        await _pedir_nombre(update, context)
        return

    hoy = ahora().date()
    start, end = db.month_bounds(hoy)
    balance = db.get_balance(start, end)

    await update.message.reply_text(
        f"*Saldo de {FAMILY_NAME} — {MESES_ES[hoy.month]} {hoy.year}*\n"
        f"Ingresos: ${balance['ingresos']:,.2f}\n"
        f"Gastos:   ${balance['gastos']:,.2f}\n"
        f"Saldo:    ${balance['saldo']:,.2f}",
        parse_mode="Markdown",
    )


async def cmd_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not db.get_member(user.id):
        await _pedir_nombre(update, context)
        return

    hoy = ahora().date()
    start, end = db.month_bounds(hoy)
    categorias = db.get_spent_by_category(start, end, "gasto")

    lines = [f"*Gastos por categoría — {MESES_ES[hoy.month]} {hoy.year}*"]
    hubo_datos = False
    for c in categorias:
        if c["total"] == 0 and not c["budget_monthly"]:
            continue
        hubo_datos = True
        alerta = ""
        if c["budget_monthly"]:
            pct = c["total"] / c["budget_monthly"] * 100
            alerta = f" ({pct:.0f}% de ${c['budget_monthly']:,.0f})"
            if c["total"] > c["budget_monthly"]:
                alerta += " ⚠️"
        lines.append(f"  {c['category_name']}: ${c['total']:,.2f}{alerta}")

    if not hubo_datos:
        lines.append("  Sin gastos registrados este mes.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cats = db.list_categories()
    lines = ["*Categorías de gasto:*"]
    for c in cats:
        if c["type"] != "gasto":
            continue
        pres = f" — presupuesto ${c['budget_monthly']:,.0f}/mes" if c["budget_monthly"] else ""
        lines.append(f"  {c['name']}{pres}")
    lines.append("")
    lines.append("*Categorías de ingreso:*")
    for c in cats:
        if c["type"] != "ingreso":
            continue
        lines.append(f"  {c['name']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_categoria_nueva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /categoria_nueva Nombre gasto|ingreso [presupuesto]"""
    if not es_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Uso: /categoria_nueva Nombre gasto|ingreso [presupuesto]\n"
            "Ejemplo: /categoria_nueva Mascotas gasto 15000"
        )
        return

    args = context.args
    presupuesto = None
    if _extraer_monto(args[-1]) is not None and args[-1].lower() not in ("gasto", "ingreso"):
        presupuesto = _extraer_monto(args[-1])
        tipo   = args[-2].lower()
        nombre = " ".join(args[:-2])
    else:
        tipo   = args[-1].lower()
        nombre = " ".join(args[:-1])

    if tipo not in ("gasto", "ingreso"):
        await update.message.reply_text("El tipo debe ser 'gasto' o 'ingreso'.")
        return
    if not nombre.strip():
        await update.message.reply_text("Falta el nombre de la categoría.")
        return

    db.create_category(nombre.strip(), tipo, presupuesto, "")
    extra = f" — presupuesto ${presupuesto:,.0f}/mes" if presupuesto else ""
    await update.message.reply_text(f"Categoría creada: *{nombre.strip()}* ({tipo}){extra}", parse_mode="Markdown")


async def cmd_presupuesto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /presupuesto Categoria monto"""
    if not es_admin(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /presupuesto Categoria monto\nEjemplo: /presupuesto Comida 80000")
        return

    monto  = _extraer_monto(context.args[-1])
    nombre = " ".join(context.args[:-1])
    if monto is None:
        await update.message.reply_text("Monto inválido.")
        return

    matches = db.find_category_by_name(nombre, "gasto")
    if not matches:
        await update.message.reply_text("No encontré esa categoría de gasto.")
        return
    if len(matches) > 1:
        await update.message.reply_text(
            f"Varios resultados: {', '.join(m['name'] for m in matches)}. Sé más específico."
        )
        return

    cat = matches[0]
    db.set_budget(cat["id"], monto)
    await update.message.reply_text(f"Presupuesto actualizado: {cat['name']} → ${monto:,.2f}/mes")


async def cmd_miembros(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        await update.message.reply_text("Solo los administradores pueden ver la lista de miembros.")
        return
    miembros = db.list_members()
    if not miembros:
        await update.message.reply_text("No hay miembros registrados aún.")
        return
    lines = [f"*Miembros de {FAMILY_NAME} ({len(miembros)}):*"]
    for m in miembros:
        lines.append(f"  - {m['name']} (ID: {m['telegram_id']})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_ver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /ver Nombre"""
    if not es_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Uso: /ver Nombre")
        return

    matches = db.find_member_by_name(" ".join(context.args))
    if not matches:
        await update.message.reply_text("No encontré a esa persona.")
        return
    if len(matches) > 1:
        await update.message.reply_text(
            f"Varios resultados: {', '.join(m['name'] for m in matches)}. Sé más específico."
        )
        return

    miembro = matches[0]
    txs = db.get_member_transactions(miembro["telegram_id"], limit=10)
    if not txs:
        await update.message.reply_text(f"No hay movimientos de {miembro['name']}.")
        return

    lines = [f"*Últimos movimientos — {miembro['name']}:*"]
    for t in txs:
        signo = "+" if t["type"] == "ingreso" else "-"
        desc  = (t["description"] or "")[:30]
        lines.append(f"  #{t['id']} {t['date']}  {signo}${t['amount']:,.2f}  {t.get('category_name') or 'Sin categoría'}  {desc}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /borrar id"""
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("Uso: /borrar id\nEjemplo: /borrar 42\n(Usá /ver para ver los IDs)")
        return
    try:
        tid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return

    tx = db.get_transaction(tid)
    if not tx:
        await update.message.reply_text("No encontré ese movimiento.")
        return
    if tx["telegram_id"] != user.id and not es_admin(user.id):
        await update.message.reply_text("Solo podés borrar tus propios movimientos.")
        return

    db.delete_transaction(tid)
    await update.message.reply_text(f"Movimiento #{tid} eliminado.")


async def cmd_corregir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Uso: /corregir id campo valor — campos: monto, categoria, descripcion"""
    if not es_admin(update.effective_user.id):
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "Uso: /corregir id campo valor\n"
            "Campos: monto, categoria, descripcion\n"
            "Ejemplo: /corregir 42 monto 5500"
        )
        return

    try:
        tid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID debe ser un número.")
        return

    campo = context.args[1].lower()
    valor = " ".join(context.args[2:])
    tx = db.get_transaction(tid)
    if not tx:
        await update.message.reply_text("No encontré ese movimiento.")
        return

    if campo == "monto":
        monto = _extraer_monto(valor)
        if monto is None:
            await update.message.reply_text("Monto inválido.")
            return
        db.update_transaction(tid, amount=monto)
    elif campo == "categoria":
        matches = db.find_category_by_name(valor, tx["type"])
        if not matches:
            await update.message.reply_text("No encontré esa categoría.")
            return
        db.update_transaction(tid, category_id=matches[0]["id"])
    elif campo == "descripcion":
        db.update_transaction(tid, description=valor)
    else:
        await update.message.reply_text("Campo inválido. Usá: monto, categoria, descripcion")
        return

    await update.message.reply_text(f"Movimiento #{tid} corregido.")


async def cmd_reporte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        await update.message.reply_text("Solo los administradores pueden generar reportes.")
        return

    hoy    = ahora().date()
    start  = date(hoy.year, 1, 1)
    end    = date(hoy.year, 12, 31)
    titulo = f"Reporte Anual {hoy.year}"

    records  = db.get_transactions_by_period(start, end)
    buffer   = build_xlsx(db, records, titulo, start, end)
    filename = f"finanzas_{hoy.year}.xlsx"
    await update.message.reply_document(
        document=buffer, filename=filename,
        caption=f"Reporte anual {hoy.year} — {FAMILY_NAME}",
    )

    buffer.seek(0)
    if send_email(buffer, filename, f"Finanzas familiares - Reporte {ahora().strftime('%d/%m/%Y')}"):
        await update.message.reply_text("Reporte también enviado por email.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info("MSG uid=%s text=%r", user.id, update.message.text)

    if await _guardar_nombre(update, context):
        return
    if not db.get_member(user.id):
        await _pedir_nombre(update, context)
        return

    await _procesar_movimiento(update, context, update.message.text.strip())


# ---------- audio de voz ----------

_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import whisper as _w
        model_name = os.getenv("WHISPER_MODEL", "base")
        _whisper_model = _w.load_model(model_name)
    return _whisper_model


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe el audio y lo procesa como si fuera texto."""
    user = update.effective_user

    if await _guardar_nombre(update, context):
        return
    if not db.get_member(user.id):
        await _pedir_nombre(update, context)
        return

    msg_espera = await update.message.reply_text("Escuchando...")

    import tempfile
    voz   = update.message.voice
    vfile = await voz.get_file()

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await vfile.download_to_drive(tmp_path)

        import asyncio, functools
        loop  = asyncio.get_event_loop()
        model = _get_whisper()
        result = await loop.run_in_executor(
            None,
            functools.partial(model.transcribe, tmp_path, language="es", fp16=False)
        )
        texto = result["text"].strip()

        try:
            await msg_espera.delete()
        except Exception:
            pass

        await _procesar_movimiento(update, context, texto)
    except Exception:
        try:
            await msg_espera.delete()
        except Exception:
            pass
        await update.message.reply_text(
            "No pude procesar el audio.\n"
            "Escribí el movimiento directamente, por ejemplo: \"gasté 5000 en supermercado\"."
        )
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# ---------- notificaciones automáticas ----------

async def job_reporte_mensual(context: CallbackContext):
    """Día 1 de cada mes a las 09:00 — envía el reporte del mes anterior."""
    hoy = ahora().date()
    if hoy.day != 1:
        return

    mes_pasado = hoy.replace(day=1) - timedelta(days=1)
    start = mes_pasado.replace(day=1)
    end   = mes_pasado
    label = f"{MESES_ES[mes_pasado.month]} {mes_pasado.year}"

    records  = db.get_transactions_by_period(start, end)
    buffer   = build_xlsx(db, records, f"Reporte {label}", start, end)
    filename = f"finanzas_{mes_pasado.year}{mes_pasado.month:02d}.xlsx"

    for admin_id in ADMIN_IDS:
        await context.bot.send_document(
            chat_id=admin_id, document=buffer, filename=filename,
            caption=f"Reporte mensual automático — {label}",
        )
        buffer.seek(0)

    send_email(buffer, filename, f"Finanzas familiares - {label}")


async def job_resumen_semanal(context: CallbackContext):
    """Domingos a las 20:00 — resumen de gastos de la semana a los administradores."""
    hoy   = ahora().date()
    start = hoy - timedelta(days=6)
    balance = db.get_balance(start, hoy)

    if balance["ingresos"] == 0 and balance["gastos"] == 0:
        return

    texto = (
        f"*Resumen semanal — {FAMILY_NAME}*\n"
        f"{start.strftime('%d/%m')} al {hoy.strftime('%d/%m')}\n\n"
        f"Ingresos: ${balance['ingresos']:,.2f}\n"
        f"Gastos:   ${balance['gastos']:,.2f}\n"
        f"Saldo:    ${balance['saldo']:,.2f}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=texto, parse_mode="Markdown")
        except Exception:
            pass


async def job_backup(context: CallbackContext):
    """Copia la base de datos a la carpeta de backups cada 6 horas."""
    db_path    = os.getenv("DB_PATH", "finanzas.db")
    backup_dir = os.getenv("BACKUP_DIR",
                            os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "backups"))
    ts     = ahora().strftime("%Y%m%d_%H%M%S")
    nombre = f"finanzas_{ts}.db"

    try:
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(db_path, os.path.join(backup_dir, nombre))
        archivos = sorted(f for f in os.listdir(backup_dir) if f.endswith(".db"))
        for viejo in archivos[:-30]:
            os.remove(os.path.join(backup_dir, viejo))
    except Exception as e:
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=f"Backup falló: {e}")
            except Exception:
                pass


async def job_alive(context: CallbackContext):
    """08:00 — confirma al admin que el bot está activo."""
    hoy = ahora()
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"Bot de finanzas activo — {hoy.strftime('%d/%m/%Y %H:%M')}",
            )
        except Exception:
            pass


# ---------- main ----------

_INSTANCE_LOCK = None


def _check_and_restore_db() -> str | None:
    """Si la DB falta o está corrupta, restaura desde el backup más reciente."""
    import sqlite3
    db_path    = os.getenv("DB_PATH", "finanzas.db")
    backup_dir = os.getenv("BACKUP_DIR",
                            os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".", "backups"))
    necesita = False

    if not os.path.exists(db_path):
        necesita = True
        print("DB no encontrada — buscando backup...")
    else:
        try:
            con = sqlite3.connect(db_path, timeout=5)
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            con.close()
            if ok != "ok":
                necesita = True
                print(f"DB corrupta ({ok}) — buscando backup...")
        except Exception as e:
            necesita = True
            print(f"Error al abrir DB ({e}) — buscando backup...")

    if not necesita or not os.path.isdir(backup_dir):
        return None

    candidatos = sorted((f for f in os.listdir(backup_dir) if f.endswith(".db")), reverse=True)
    if not candidatos:
        print("No se encontró ningún backup. El sistema iniciará con DB vacía.")
        return None

    mejor = os.path.join(backup_dir, candidatos[0])
    shutil.copy2(mejor, db_path)
    print(f"DB restaurada desde: {mejor}")
    return mejor


def _verificar_instancia_unica():
    """Usa un socket como lock. Si ya hay un bot corriendo, esta instancia sale sin error."""
    import socket, sys
    global _INSTANCE_LOCK
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 47391))
        _INSTANCE_LOCK = sock
    except OSError:
        print("Bot ya está corriendo. Esta instancia no iniciará.")
        sys.exit(0)


def main():
    import asyncio

    _verificar_instancia_unica()
    _check_and_restore_db()
    asyncio.set_event_loop(asyncio.new_event_loop())

    if not TOKEN:
        raise ValueError("Falta la variable de entorno TELEGRAM_TOKEN")
    if not ADMIN_IDS:
        print("ADVERTENCIA: ADMIN_IDS no configurado. Nadie podrá generar reportes ni ajustar presupuestos.")

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram.error import NetworkError, TimedOut
        err = context.error
        if isinstance(err, (NetworkError, TimedOut)):
            logger.warning("RED: %s", err)
        else:
            logger.error("ERROR inesperado: %s", err, exc_info=err)

    app = (Application.builder()
           .token(TOKEN)
           .read_timeout(120)
           .write_timeout(120)
           .connect_timeout(30)
           .build())

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start",            cmd_start))
    app.add_handler(CommandHandler("ayuda",            cmd_ayuda))
    app.add_handler(CommandHandler("help",             cmd_ayuda))
    app.add_handler(CommandHandler("gasto",            cmd_gasto))
    app.add_handler(CommandHandler("ingreso",          cmd_ingreso))
    app.add_handler(CommandHandler("saldo",            cmd_saldo))
    app.add_handler(CommandHandler("resumen",          cmd_resumen))
    app.add_handler(CommandHandler("categorias",       cmd_categorias))
    app.add_handler(CommandHandler("categoria_nueva",  cmd_categoria_nueva))
    app.add_handler(CommandHandler("presupuesto",      cmd_presupuesto))
    app.add_handler(CommandHandler("miembros",         cmd_miembros))
    app.add_handler(CommandHandler("ver",              cmd_ver))
    app.add_handler(CommandHandler("borrar",           cmd_borrar))
    app.add_handler(CommandHandler("corregir",         cmd_corregir))
    app.add_handler(CommandHandler("reporte",          cmd_reporte))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE,                   handle_voice))

    jq = app.job_queue
    jq.run_daily(job_reporte_mensual, time=dt_time(hour=9,  minute=0,  tzinfo=TIMEZONE))
    jq.run_daily(job_resumen_semanal, time=dt_time(hour=20, minute=0,  tzinfo=TIMEZONE), days=(6,))
    jq.run_daily(job_alive,           time=dt_time(hour=8,  minute=0,  tzinfo=TIMEZONE))
    for _h in (6, 12, 18, 0):
        jq.run_daily(job_backup, time=dt_time(hour=_h, minute=0, tzinfo=TIMEZONE))

    print("Bot de finanzas familiares iniciado. Esperando mensajes...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
