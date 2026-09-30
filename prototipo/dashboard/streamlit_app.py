"""Dashboard Streamlit del caso Smart Parking Albacete.

En modo real consume la API REST de AWS. En modo de publicación genera una
simulación determinista desde las mismas 40 plazas, sin recursos cloud activos.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import plotly.express as px
import pydeck as pdk
import requests
import streamlit as st

from demo_data import build_demo_snapshot

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "infra" / "infra_state.json"
SEED_FILE = ROOT / "infra" / "parking_zone_seed.json"
VALID_MODES = {"auto", "demo", "live"}


def _resolve_api_base() -> str:
    if os.environ.get("API_BASE_URL"):
        return os.environ["API_BASE_URL"].rstrip("/")
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as stream:
            state = json.load(stream)
        if state.get("apiBaseUrl"):
            return str(state["apiBaseUrl"]).rstrip("/")
    return ""


mode = os.environ.get("SMART_PARKING_MODE", "auto").lower()
if mode not in VALID_MODES:
    st.error("SMART_PARKING_MODE debe ser auto, demo o live.")
    st.stop()

api_base = _resolve_api_base()
demo_mode = mode == "demo" or (mode == "auto" and not api_base)

st.set_page_config(
    page_title="Smart Parking Albacete",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 3.5rem; padding-bottom: 0.4rem; padding-left: 1rem; padding-right: 1rem; max-width: 100%;}
      @media (max-width: 768px) {
        .block-container {padding-top: 4.2rem;}
      }
      [data-testid="stMetric"] {padding: 4px 8px;}
      [data-testid="stMetricLabel"] {font-size: 0.8rem;}
      [data-testid="stMetricValue"] {font-size: 1.6rem;}
      h1 {font-size: 1.4rem !important; margin-bottom: 0.2rem;}
      h2, h3 {font-size: 1.0rem !important; margin-top: 0.2rem; margin-bottom: 0.2rem;}
      .stPlotlyChart, .stPyDeckChart, .stDataFrame {margin-top: 0 !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Configuración")
    if demo_mode:
        st.caption("Modo simulación: no se conecta a AWS.")
        api_input = ""
    else:
        api_input = st.text_input("URL base de la API", value=api_base)
    refresh_seconds = st.slider("Refresco automático (s)", 0, 30, 10)
    if st.button("Forzar refresco"):
        st.rerun()


@st.cache_data(ttl=4)
def fetch_json(url: str) -> dict:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


if demo_mode:
    snapshot = build_demo_snapshot(SEED_FILE)
    spots = snapshot["spots"]
    zones = snapshot["zones"]
    st.info(
        "Simulación reproducible para portfolio: los estados cambian cada minuto sobre "
        "las 40 ubicaciones del caso, sin IoT Core, Lambda ni DynamoDB activos."
    )
else:
    api_input = api_input or api_base
    if not api_input:
        st.warning("Define API_BASE_URL o usa SMART_PARKING_MODE=demo.")
        st.stop()
    try:
        spots = fetch_json(f"{api_input}/spots").get("items", [])
        zones = fetch_json(f"{api_input}/zones").get("items", [])
    except Exception as error:
        st.error(f"No se pudo conectar con la API: {error}")
        st.stop()

total = len(spots)
free = sum(1 for spot in spots if spot.get("status") == "free")
occupied = sum(1 for spot in spots if spot.get("status") == "occupied")
unknown = total - free - occupied
rate = occupied / total * 100 if total else 0.0

st.markdown("### Smart Parking Albacete · Panel de operador")
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Plazas", total)
k2.metric("Libres", free)
k3.metric("Ocupadas", occupied)
k4.metric("Sin datos", unknown)
k5.metric("Ocupación", f"{rate:.1f} %")

row1_left, row1_right = st.columns(2, gap="small")
row2_left, row2_right = st.columns(2, gap="small")

with row1_left:
    st.markdown("**Mapa de plazas**")
    if spots:
        frame = pd.DataFrame(spots)
        frame["lat"] = frame["lat"].astype(float)
        frame["lon"] = frame["lon"].astype(float)
        colors = {"free": [46, 204, 113], "occupied": [231, 76, 60]}
        frame["color"] = frame["status"].apply(lambda state: colors.get(state, [127, 140, 141]))
        layer = pdk.Layer(
            "ScatterplotLayer",
            frame,
            get_position="[lon, lat]",
            get_color="color",
            get_radius=12,
            pickable=True,
        )
        view = pdk.ViewState(latitude=38.9796, longitude=-1.8524, zoom=15, pitch=0)
        st.pydeck_chart(
            pdk.Deck(
                layers=[layer],
                initial_view_state=view,
                map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
                tooltip={"text": "{spotId}\n{street}\nEstado: {status}"},
            ),
            height=360,
        )

with row1_right:
    st.markdown("**KPIs por subzona**")
    if zones:
        zone_frame = pd.DataFrame(zones).sort_values("zoneId")
        chart = px.bar(
            zone_frame,
            x="zoneId",
            y=["free", "occupied", "unknown"],
            labels={"value": "Plazas", "zoneId": "Subzona", "variable": "Estado"},
            color_discrete_map={"free": "#2ecc71", "occupied": "#e74c3c", "unknown": "#95a5a6"},
        )
        chart.update_layout(
            height=360,
            margin=dict(l=0, r=0, t=10, b=0),
            barmode="stack",
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1),
        )
        st.plotly_chart(chart, use_container_width=True)

with row2_left:
    st.markdown("**Evolución temporal**")
    zone_options = [zone["zoneId"] for zone in zones]
    if zone_options:
        selected_zone = st.selectbox("Subzona", zone_options, label_visibility="collapsed")
        try:
            items = (
                snapshot["history"][selected_zone]
                if demo_mode
                else fetch_json(f"{api_input}/zones/{selected_zone}/kpis?limit=200").get("items", [])
            )
            if items:
                series = pd.DataFrame(items).sort_values("windowEnd")
                series["occupancyRate"] = series["occupancyRate"].astype(float) * 100
                line = px.line(
                    series,
                    x="windowEnd",
                    y="occupancyRate",
                    labels={"occupancyRate": "% Ocupación", "windowEnd": "Tiempo"},
                )
                line.update_layout(height=280, margin=dict(l=0, r=0, t=5, b=0))
                st.plotly_chart(line, use_container_width=True)
            else:
                st.info("Sin agregados disponibles.")
        except Exception as error:
            st.warning(f"KPIs temporales: {error}")

with row2_right:
    st.markdown("**Detalle de plazas**")
    if spots:
        spots_frame = pd.DataFrame(spots)
        columns = ["spotId", "zoneId", "street", "status", "batteryLevel"]
        st.dataframe(
            spots_frame[[column for column in columns if column in spots_frame.columns]],
            use_container_width=True,
            hide_index=True,
            height=320,
        )

if refresh_seconds:
    time.sleep(refresh_seconds)
    st.rerun()
