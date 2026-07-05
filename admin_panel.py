import os
import sys
from datetime import date, datetime

import streamlit as st
import pandas as pd
import pytz
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sys.path.insert(0, os.path.dirname(__file__))

from database import Database
from reports import build_xlsx, MESES_ES

TIMEZONE = pytz.timezone("America/Argentina/Buenos_Aires")
FAMILY_NAME = os.getenv("FAMILY_NAME", "Finanzas Familiares")


def ahora():
    return datetime.now(TIMEZONE)


@st.cache_resource
def get_db():
    return Database()


db = get_db()

st.set_page_config(page_title="Finanzas Familiares", page_icon="💰", layout="wide")

st.markdown("""
<style>
  .stDeployButton { display: none !important; }
  #MainMenu { visibility: hidden; }
  footer    { visibility: hidden; }
  div[data-testid="stMetric"] {
    background: rgba(127,127,127,0.08);
    border-radius: 12px;
    padding: 20px 24px;
    border: 1px solid rgba(127,127,127,0.15);
  }
</style>
""", unsafe_allow_html=True)

st.title(f"💰 {FAMILY_NAME}")
st.caption(f"Actualizado: {ahora().strftime('%d/%m/%Y %H:%M')}")

tab_hoy, tab_mov, tab_cat, tab_rep = st.tabs(
    ["📅 Este mes", "🧾 Movimientos", "🏷️ Categorías", "📊 Reportes"]
)

# ── TAB 1: Resumen del mes ────────────────────────────────────────────────
with tab_hoy:
    hoy = ahora().date()
    start, end = db.month_bounds(hoy)
    balance = db.get_balance(start, end)

    c1, c2, c3 = st.columns(3)
    c1.metric("Ingresos del mes", f"${balance['ingresos']:,.2f}")
    c2.metric("Gastos del mes",   f"${balance['gastos']:,.2f}")
    c3.metric("Saldo",            f"${balance['saldo']:,.2f}")

    st.divider()
    st.subheader(f"Gastos por categoría — {MESES_ES[hoy.month]} {hoy.year}")

    cats = db.get_spent_by_category(start, end, "gasto")
    filas = []
    for c in cats:
        if c["total"] == 0 and not c["budget_monthly"]:
            continue
        pct = (c["total"] / c["budget_monthly"] * 100) if c["budget_monthly"] else None
        filas.append({
            "Categoría":    c["category_name"],
            "Gastado":      f"${c['total']:,.2f}",
            "Presupuesto":  f"${c['budget_monthly']:,.2f}" if c["budget_monthly"] else "–",
            "% usado":      f"{pct:.0f}%" if pct is not None else "–",
            "Estado":       "🔴 Excedido" if (pct is not None and pct > 100) else ("🟢 OK" if pct is not None else "–"),
        })
    if filas:
        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
    else:
        st.info("Sin gastos registrados este mes.")

    st.divider()
    st.subheader("Por miembro")
    miembros = db.list_members()
    filas_m = []
    for m in miembros:
        bal = db.get_balance(start, end, m["telegram_id"])
        filas_m.append({
            "Miembro":  m["name"],
            "Ingresos": f"${bal['ingresos']:,.2f}",
            "Gastos":   f"${bal['gastos']:,.2f}",
            "Saldo":    f"${bal['saldo']:,.2f}",
        })
    if filas_m:
        st.dataframe(pd.DataFrame(filas_m), use_container_width=True, hide_index=True)
    else:
        st.info("No hay miembros registrados aún.")

# ── TAB 2: Movimientos ────────────────────────────────────────────────────
with tab_mov:
    st.subheader("Últimos movimientos")

    hoy = ahora().date()
    start, end = db.month_bounds(hoy)
    col_a, col_b = st.columns(2)
    with col_a:
        start_sel = st.date_input("Desde", value=start)
    with col_b:
        end_sel = st.date_input("Hasta", value=end)

    records = db.get_transactions_by_period(start_sel, end_sel)
    records = sorted(records, key=lambda r: r["created_at"], reverse=True)

    if records:
        filas = []
        for r in records:
            signo = "+" if r["type"] == "ingreso" else "-"
            filas.append({
                "ID":         r["id"],
                "Fecha":      r["date"],
                "Miembro":    r["member_name"],
                "Tipo":       "Ingreso" if r["type"] == "ingreso" else "Gasto",
                "Categoría":  r.get("category_name") or "Sin categoría",
                "Monto":      f"{signo}${r['amount']:,.2f}",
                "Descripción": r.get("description") or "",
            })
        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
    else:
        st.info("Sin movimientos en el período seleccionado.")

# ── TAB 3: Categorías ──────────────────────────────────────────────────────
with tab_cat:
    st.subheader("Categorías de gasto")
    cats_gasto = db.list_categories("gasto")
    filas_g = [{
        "Nombre": c["name"],
        "Presupuesto mensual": f"${c['budget_monthly']:,.2f}" if c["budget_monthly"] else "Sin límite",
    } for c in cats_gasto]
    st.dataframe(pd.DataFrame(filas_g), use_container_width=True, hide_index=True)

    st.subheader("Categorías de ingreso")
    cats_ingreso = db.list_categories("ingreso")
    st.dataframe(pd.DataFrame([{"Nombre": c["name"]} for c in cats_ingreso]),
                 use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Ajustar presupuesto")
    if cats_gasto:
        cat_sel = st.selectbox("Categoría", options=[c["name"] for c in cats_gasto])
        nuevo_presupuesto = st.number_input("Presupuesto mensual", min_value=0.0, step=1000.0)
        if st.button("💾 Guardar presupuesto"):
            cat_obj = next(c for c in cats_gasto if c["name"] == cat_sel)
            db.set_budget(cat_obj["id"], nuevo_presupuesto if nuevo_presupuesto > 0 else None)
            st.success(f"Presupuesto de {cat_sel} actualizado.")
            st.rerun()

    st.divider()
    st.subheader("Nueva categoría")
    col1, col2, col3 = st.columns(3)
    with col1:
        nombre_nueva = st.text_input("Nombre", key="nombre_categoria_nueva")
    with col2:
        tipo_nueva = st.selectbox("Tipo", options=["gasto", "ingreso"], key="tipo_categoria_nueva")
    with col3:
        presupuesto_nueva = st.number_input("Presupuesto (opcional)", min_value=0.0, step=1000.0, key="presupuesto_categoria_nueva")
    if st.button("➕ Crear categoría"):
        if nombre_nueva.strip():
            db.create_category(nombre_nueva.strip(), tipo_nueva,
                                presupuesto_nueva if presupuesto_nueva > 0 else None, "")
            st.success(f"Categoría creada: {nombre_nueva.strip()}")
            st.rerun()
        else:
            st.warning("Escribí el nombre de la categoría.")

# ── TAB 4: Reportes ────────────────────────────────────────────────────────
with tab_rep:
    st.subheader("Descargar reporte XLSX")

    hoy = ahora().date()
    modo = st.radio("Período", ["Mes actual", "Año completo", "Rango personalizado"])
    if modo == "Mes actual":
        start_r, end_r = db.month_bounds(hoy)
        label_r = f"{MESES_ES[hoy.month]} {hoy.year}"
    elif modo == "Año completo":
        start_r = date(hoy.year, 1, 1)
        end_r   = date(hoy.year, 12, 31)
        label_r = f"Año {hoy.year}"
    else:
        start_r = st.date_input("Desde", value=hoy.replace(day=1), key="rango_desde")
        end_r   = st.date_input("Hasta", value=hoy, key="rango_hasta")
        label_r = f"{start_r.strftime('%d/%m')} al {end_r.strftime('%d/%m/%Y')}"

    if st.button("📥 Generar Excel"):
        with st.spinner("Generando Excel..."):
            records = db.get_transactions_by_period(start_r, end_r)
            buf = build_xlsx(db, records, f"Reporte {label_r}", start_r, end_r)
        filename = f"finanzas_{start_r.strftime('%Y%m%d')}_{end_r.strftime('%Y%m%d')}.xlsx"
        st.download_button(
            label="⬇️ Descargar Excel",
            data=buf,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
