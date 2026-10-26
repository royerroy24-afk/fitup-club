"""
Meta Ads Daily Reviewer
-----------------------
Audita las campañas de una cuenta publicitaria de Meta usando la Marketing API
y genera un reporte diario en la carpeta reports/.

IMPORTANTE: este script SOLO LEE datos y recomienda. No pausa, no edita
presupuestos ni cambia nada en tu cuenta.

Variables de entorno (las pasa el workflow de GitHub Actions):
  META_ACCESS_TOKEN   -> token (guardado en GitHub Secrets)
  META_AD_ACCOUNT_ID  -> ej. act_2301495497021946
  META_API_VERSION    -> ej. v21.0
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
TOKEN = os.getenv("META_ACCESS_TOKEN", "").strip()
ACCOUNT_ID = os.getenv("META_AD_ACCOUNT_ID", "").strip()
API_VERSION = os.getenv("META_API_VERSION", "v21.0").strip()
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

# Umbrales de alerta (ajústalos a tu negocio)
MAX_FRECUENCIA = 3.2       # frecuencia alta = audiencia saturada
MIN_CTR = 1.2              # % de clics sobre impresiones
GASTO_MIN_SIN_RESULTADOS = 15.0  # gasto (moneda de la cuenta) sin resultados
FACTOR_CPC_ALTO = 1.4      # CPC mayor a 1.5x el promedio de la cuenta

# Acciones que cuentan como "resultado"
ACCIONES_RESULTADO = {
    "lead",
    "onsite_conversion.lead_grouped",
    "offsite_conversion.fb_pixel_lead",
    "offsite_conversion.fb_pixel_purchase",
    "purchase",
    "onsite_conversion.messaging_conversation_started_7d",
    "onsite_conversion.total_messaging_connection",
    "messaging_conversation_started_7d",
    "contact",
    "complete_registration",
}

REPORTS_DIR = "reports"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def fallar(mensaje: str) -> None:
    print(f"ERROR: {mensaje}", file=sys.stderr)
    sys.exit(1)


def validar_config() -> None:
    if not TOKEN:
        fallar(
            "Falta META_ACCESS_TOKEN. Créalo en GitHub: Ajustes > Secrets and "
            "variables > Actions > New repository secret."
        )
    if not ACCOUNT_ID:
        fallar("Falta META_AD_ACCOUNT_ID.")
    if not ACCOUNT_ID.startswith("act_"):
        fallar("META_AD_ACCOUNT_ID debe empezar con 'act_'.")


def llamar_api(ruta: str, params: dict) -> list:
    """Hace GET a la Graph API siguiendo la paginación. Nunca imprime el token."""
    params = {**params, "access_token": TOKEN}
    url = f"{BASE_URL}/{ruta}"
    resultados = []

    while url:
        try:
            resp = requests.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            fallar(f"No se pudo conectar con la API de Meta: {type(exc).__name__}")

        try:
            data = resp.json()
        except ValueError:
            fallar(f"Respuesta no válida de Meta (HTTP {resp.status_code}).")

        if "error" in data:
            err = data["error"]
            fallar(
                f"Meta API respondió con error {err.get('code')}: "
                f"{err.get('message')}"
            )

        resultados.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
        params = {}  # la URL "next" ya trae todos los parámetros

    return resultados


def a_float(valor) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def contar_resultados(acciones) -> float:
    if not acciones:
        return 0.0
    total = 0.0
    for accion in acciones:
        if accion.get("action_type") in ACCIONES_RESULTADO:
            total += a_float(accion.get("value"))
    return total


# ---------------------------------------------------------------------------
# Obtención de datos
# ---------------------------------------------------------------------------
def obtener_info_cuenta() -> dict:
    params = {
        "fields": "name,currency,account_status,timezone_name",
        "access_token": TOKEN,
    }
    resp = requests.get(f"{BASE_URL}/{ACCOUNT_ID}", params=params, timeout=60)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        fallar(f"Meta API error {err.get('code')}: {err.get('message')}")
    return data


def obtener_insights() -> list:
    return llamar_api(
        f"{ACCOUNT_ID}/insights",
        {
            "level": "campaign",
            "date_preset": "yesterday",
            "fields": (
                "campaign_id,campaign_name,spend,impressions,reach,clicks,"
                "ctr,cpc,cpm,frequency,actions"
            ),
            "limit": 200,
        },
    )


def obtener_estado_campanas() -> dict:
    campanas = llamar_api(
        f"{ACCOUNT_ID}/campaigns",
        {
            "fields": "id,name,effective_status,daily_budget,lifetime_budget",
            "limit": 200,
        },
    )
    return {c["id"]: c for c in campanas}


# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
def analizar(insights: list, estados: dict):
    filas = []
    gasto_total = 0.0
    clics_total = 0.0

    for ins in insights:
        gasto = a_float(ins.get("spend"))
        clics = a_float(ins.get("clicks"))
        gasto_total += gasto
        clics_total += clics

    cpc_promedio = (gasto_total / clics_total) if clics_total else 0.0

    for ins in insights:
        gasto = a_float(ins.get("spend"))
        ctr = a_float(ins.get("ctr"))
        cpc = a_float(ins.get("cpc"))
        frecuencia = a_float(ins.get("frequency"))
        resultados = contar_resultados(ins.get("actions"))
        costo_resultado = (gasto / resultados) if resultados else None
        estado = estados.get(ins.get("campaign_id"), {}).get(
            "effective_status", "DESCONOCIDO"
        )

        alertas = []
        if frecuencia > MAX_FRECUENCIA:
            alertas.append(
                f"Frecuencia alta ({frecuencia:.2f}): la audiencia está "
                "saturada. Renueva creativos o amplía la audiencia."
            )
        if ctr < MIN_CTR and a_float(ins.get("impressions")) > 500:
            alertas.append(
                f"CTR bajo ({ctr:.2f}%): el anuncio no llama la atención. "
                "Prueba nuevos textos o imágenes."
            )
        if gasto >= GASTO_MIN_SIN_RESULTADOS and resultados == 0:
            alertas.append(
                f"Gastó {gasto:.2f} sin resultados: considera pausar o "
                "revisar la segmentación y la oferta."
            )
        if cpc_promedio and cpc > cpc_promedio * FACTOR_CPC_ALTO:
            alertas.append(
                f"CPC alto ({cpc:.2f}) frente al promedio de la cuenta "
                f"({cpc_promedio:.2f})."
            )
        if not alertas and resultados > 0:
            alertas.append("Buen desempeño: considera subir presupuesto poco a poco.")

        filas.append(
            {
                "campana": ins.get("campaign_name", "Sin nombre"),
                "estado": estado,
                "gasto": gasto,
                "impresiones": a_float(ins.get("impressions")),
                "clics": a_float(ins.get("clicks")),
                "ctr": ctr,
                "cpc": cpc,
                "frecuencia": frecuencia,
                "resultados": resultados,
                "costo_resultado": costo_resultado,
                "alertas": alertas,
            }
        )

    filas.sort(key=lambda f: f["gasto"], reverse=True)
    return filas, gasto_total


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def construir_reporte(cuenta: dict, filas: list, gasto_total: float, fecha: str) -> str:
    moneda = cuenta.get("currency", "")
    nombre = cuenta.get("name", ACCOUNT_ID)
    lineas = [
        f"# Reporte diario de Meta Ads - {fecha}",
        "",
        f"**Cuenta:** {nombre} ({ACCOUNT_ID})",
        f"**Periodo analizado:** ayer",
        f"**Gasto total:** {gasto_total:.2f} {moneda}",
        f"**Campañas con actividad:** {len(filas)}",
        "",
    ]

    if not filas:
        lineas += [
            "No hubo campañas con gasto o impresiones ayer.",
            "",
            "Revisa que haya campañas activas y que el método de pago funcione.",
        ]
        return "\n".join(lineas)

    lineas += [
        "## Resumen por campaña",
        "",
        "| Campaña | Estado | Gasto | CTR % | CPC | Frecuencia | Resultados | Costo/res. |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in filas:
        costo = f"{f['costo_resultado']:.2f}" if f["costo_resultado"] is not None else "-"
        lineas.append(
            f"| {f['campana']} | {f['estado']} | {f['gasto']:.2f} | "
            f"{f['ctr']:.2f} | {f['cpc']:.2f} | {f['frecuencia']:.2f} | "
            f"{f['resultados']:.0f} | {costo} |"
        )

    lineas += ["", "## Recomendaciones", ""]
    for f in filas:
        lineas.append(f"### {f['campana']}")
        for alerta in f["alertas"]:
            lineas.append(f"- {alerta}")
        lineas.append("")

    lineas += [
        "---",
        "Este reporte es solo informativo. Ningún cambio se aplicó en la cuenta.",
    ]
    return "\n".join(lineas)


def guardar(reporte_md: str, filas: list, fecha: str) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ruta_md = os.path.join(REPORTS_DIR, f"reporte_{fecha}.md")
    ruta_json = os.path.join(REPORTS_DIR, f"reporte_{fecha}.json")

    with open(ruta_md, "w", encoding="utf-8") as f:
        f.write(reporte_md)
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False, indent=2)

    print(f"Reporte guardado en {ruta_md} y {ruta_json}")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def main() -> None:
    validar_config()

    # Fecha en hora de Perú (UTC-5) para nombrar el reporte
    hoy_peru = datetime.now(timezone.utc) - timedelta(hours=5)
    fecha = hoy_peru.strftime("%Y-%m-%d")

    print(f"Auditando cuenta {ACCOUNT_ID} (API {API_VERSION})...")

    cuenta = obtener_info_cuenta()
    insights = obtener_insights()
    estados = obtener_estado_campanas()

    filas, gasto_total = analizar(insights, estados)
    reporte = construir_reporte(cuenta, filas, gasto_total, fecha)

    print(reporte)
    guardar(reporte, filas, fecha)


if __name__ == "__main__":
    main()
