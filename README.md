# Finanzas_Flia — Bot de Telegram para finanzas familiares

Sistema para llevar las finanzas de la familia entre todos sus miembros, basado en un bot de Telegram. Cada uno registra sus gastos e ingresos con un mensaje natural desde el celular; los administradores reciben alertas de presupuesto y reportes automáticos en Excel, y pueden monitorear todo en tiempo real desde un panel web.

---

## Cómo funciona

### Para cualquier miembro de la familia

Se abre el chat del bot en Telegram y se escribe un mensaje natural. No hace falta aprender comandos.

**Registrar un gasto:**
```
gasté 5000 en supermercado
```
```
pagué 3000 de nafta
```
```
/gasto 5000 comida supermercado
```

**Registrar un ingreso:**
```
cobré 200000 de sueldo
```
```
recibí 5000
```
```
/ingreso 200000 sueldo
```

**Ver el saldo del mes:**
```
/saldo
```

**Ver los gastos por categoría:**
```
/resumen
```

El bot responde confirmando el movimiento con el monto, la categoría detectada automáticamente y la fecha:

```
Gasto registrado (#128)
Quién:     Juan
Monto:     $5,000.00
Categoría: Comida
Fecha:     05/07/2026
```

---

### Primer uso (registro automático)

La primera vez que alguien le escribe al bot, el sistema le pide el nombre:

```
Bot: ¡Hola! Para registrarte en las finanzas familiares escribí tu nombre.
Vos: Juan
Bot: ¡Bienvenido/a, Juan! Ya estás registrado en Familia Pérez.
```

A partir de ese momento, el bot reconoce a la persona por su cuenta de Telegram. No hace falta identificarse nunca más.

---

## Detección automática de categoría

Cada mensaje se analiza para detectar:
1. **El monto** — el primer número del mensaje (acepta formatos como `5000`, `5.000` o `5.000,50`).
2. **El tipo** — gasto o ingreso, según palabras clave (`gasté`, `pagué`, `compré` vs. `cobré`, `recibí`, `gané`).
3. **La categoría** — según palabras clave asociadas a cada categoría (por ejemplo "nafta" → Transporte, "sueldo" → Sueldo). Si no matchea ninguna, se asigna a "Otros gastos" / "Otros ingresos".

Los administradores pueden crear categorías nuevas y asignarles presupuesto mensual con `/categoria_nueva` y `/presupuesto`.

---

## Alertas de presupuesto

Cada categoría de gasto puede tener un presupuesto mensual. Apenas el gasto acumulado del mes cruza ese límite, el bot avisa automáticamente a los administradores:

```
⚠️ Presupuesto excedido — Comida
Gastado: $82,500.00 / $80,000.00
Mes: Julio 2026
```

---

## Reporte Excel

El día 1 de cada mes el bot envía automáticamente un archivo `.xlsx` con el resumen del mes anterior a los administradores, por Telegram y por email. También se puede generar en cualquier momento con `/reporte` (año completo) o desde el panel web (cualquier rango de fechas).

### Estructura del archivo

**Hoja "Resumen"** — ingresos, gastos y saldo totales, desglose por miembro, y gastos por categoría comparados contra el presupuesto (resaltando en rojo las categorías excedidas).

**Una hoja por miembro** — el detalle completo de sus movimientos del período: fecha, tipo, categoría, monto y descripción.

---

## Panel web del administrador

Acceso: `http://localhost:8501`

### Pestaña "Este mes"
- Métricas de ingresos, gastos y saldo del mes en curso.
- Gastos por categoría con porcentaje usado del presupuesto.
- Saldo individual de cada miembro.

### Pestaña "Movimientos"
- Tabla de todos los movimientos en un rango de fechas configurable.

### Pestaña "Categorías"
- Lista de categorías de gasto e ingreso, con sus presupuestos.
- Formulario para ajustar presupuestos y crear categorías nuevas.

### Pestaña "Reportes"
- Selector de período: mes actual, año completo o rango personalizado.
- Botón para generar y descargar el Excel directamente desde el navegador.

---

## Comandos del bot

### Todos los miembros

| Comando | Ejemplo | Acción |
|---------|---------|--------|
| `/gasto monto categoria descripcion` | `/gasto 5000 comida supermercado` | Registra un gasto |
| `/ingreso monto categoria descripcion` | `/ingreso 200000 sueldo` | Registra un ingreso |
| `/saldo` | `/saldo` | Ingresos, gastos y saldo del mes actual |
| `/resumen` | `/resumen` | Gastos por categoría del mes, con % de presupuesto usado |
| `/categorias` | `/categorias` | Lista todas las categorías disponibles |
| `/borrar id` | `/borrar 42` | Elimina un movimiento propio |
| `/ayuda` | `/ayuda` | Ver todos los comandos |

### Solo administradores

| Comando | Ejemplo | Acción |
|---------|---------|--------|
| `/categoria_nueva Nombre gasto\|ingreso [presupuesto]` | `/categoria_nueva Mascotas gasto 15000` | Crea una categoría nueva |
| `/presupuesto Categoria monto` | `/presupuesto Comida 80000` | Fija o actualiza el presupuesto mensual |
| `/miembros` | `/miembros` | Lista todos los miembros registrados |
| `/ver Nombre` | `/ver Juan` | Últimos 10 movimientos de esa persona |
| `/corregir id campo valor` | `/corregir 42 monto 5500` | Corrige monto, categoría o descripción |
| `/reporte` | `/reporte` | Genera y envía el Excel del año en curso |

---

## Notificaciones automáticas

| Cuándo | Qué hace |
|--------|----------|
| 08:00 | Confirma al admin que el bot está activo |
| Al cruzar un presupuesto | Alerta inmediata al admin, apenas se registra el gasto que lo excede |
| Domingos 20:00 | Resumen semanal de ingresos, gastos y saldo a los administradores |
| Día 1 de cada mes, 09:00 | Reporte del mes anterior (Excel) por Telegram y email |
| Cada 6 horas | Backup automático de la base de datos |

---

## Arquitectura del sistema

```
Finanzas_Flia/
├── bot.py            # Bot de Telegram — lógica de comandos y mensajes
├── database.py       # Base de datos SQLite — miembros, movimientos, categorías, presupuestos
├── reports.py        # Generación de XLSX y envío de email
├── admin_panel.py    # Panel web Streamlit (localhost:8501)
├── finanzas.db        # Base de datos (generada al primer arranque)
├── backups/           # Copias periódicas de la base de datos
├── .env               # Variables de entorno (token, email, configuración)
└── requirements.txt    # Dependencias Python
```

### Base de datos (SQLite)

| Tabla | Contenido |
|-------|-----------|
| `members` | Miembros de la familia registrados (Telegram ID, nombre) |
| `categories` | Categorías de gasto/ingreso, presupuesto mensual y palabras clave para detección automática |
| `transactions` | Movimientos: tipo, categoría, monto, descripción y fecha |

---

## Configuración (.env)

```env
TELEGRAM_TOKEN=tu_token_de_botfather
FAMILY_NAME=Familia Pérez
ADMIN_IDS=tu_id_de_telegram

DB_PATH=C:\ruta\finanzas.db
BACKUP_DIR=C:\ruta\backups

EMAIL_FROM=tucuenta@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_TO=destino@gmail.com

WHISPER_MODEL=base
```

---

## Instalación

### Requisitos
- Python 3.10+
- Cuenta de Telegram

### Pasos

**1. Crear el bot en Telegram**

Buscar `@BotFather` en Telegram → `/newbot` → guardar el token.

Para obtener tu ID de admin: buscar `@userinfobot` → enviar cualquier mensaje → copiar el `Id`.

**2. Instalar dependencias**

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt   # Windows: venv\Scripts\pip
```

**3. Configurar el archivo .env**

Copiar `.env.example` a `.env` y completar los valores.

**4. Arrancar**

```bash
venv/bin/python bot.py
venv/bin/streamlit run admin_panel.py --server.port 8501
```

El panel queda disponible en `http://localhost:8501`.

### Despliegue en la nube

El repositorio incluye configuración lista para **Fly.io** (`fly.toml`), **Railway** (`railway.toml`) y **Docker** (`Dockerfile`).

---

## Stack tecnológico

| Componente | Tecnología |
|-----------|-----------|
| Bot de mensajería | python-telegram-bot 21.6 |
| Base de datos | SQLite (via Python sqlite3) |
| Panel web | Streamlit 1.58 |
| Reportes Excel | openpyxl 3.1 |
| Scheduler interno | APScheduler (via python-telegram-bot job-queue) |
| Email | smtplib (Gmail SMTP SSL) |
| Transcripción de audio | openai-whisper |
| Lenguaje | Python 3.12 |
