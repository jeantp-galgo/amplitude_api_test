"""
Cotizaciones - extraccion y merge desde Amplitude.

3 queries a /api/2/events/segmentation:
  1) Product Updated      -> lead_id, sales_channel, brand, model, color, price_net
  2) Personal info completed -> lead_id, dni
  3) Deal Assigned        -> lead_id, deal_id, resolution

Merge por lead_id. Output: cotizaciones_YYYYMMDD.csv
"""

import os
import json
import base64
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()

API_KEY = os.environ["AMPLITUDE_API_KEY"]
API_SECRET = os.environ["AMPLITUDE_API_SECRET"]

_token = base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
AUTH_HEADER = {"Authorization": f"Basic {_token}"}
ENDPOINT = "https://amplitude.com/api/2/events/segmentation"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
FECHA_CORTE = date.today()
DIAS_VENTANA = 12
DIAS_BUCKET = 3

FILTROS_BASE = [
    {"subprop_type": "event", "subprop_key": "country_code",  "subprop_op": "is", "subprop_value": ["MX"]},
    {"subprop_type": "event", "subprop_key": "sales_channel", "subprop_op": "is", "subprop_value": ["dealer"]},
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def parse_amplitude_response(resp_json, group_by_fields):
    data = resp_json.get("data", {})
    series = data.get("series", [])
    labels = data.get("seriesLabels", [])
    x_values = data.get("xValues", [])

    rows = []
    n_cols = len(group_by_fields)

    for i, serie in enumerate(series):
        label = labels[i] if i < len(labels) else ""

        # Amplitude devuelve labels como string ("v1, v2, ...") con 1 group_by,
        # o como lista (["v1", "v2", ...]) con multiples group_by.
        if isinstance(label, list):
            parts = [str(p) for p in label]
        else:
            parts = label.split(", ")
            if len(parts) < n_cols and "; " in label:
                parts = label.split("; ")

        if len(parts) < n_cols:
            parts = parts + ["(none)"] * (n_cols - len(parts))

        label_map = {field: parts[k] for k, field in enumerate(group_by_fields)}

        for d, value in enumerate(serie):
            if value and value > 0:
                row = {"date": x_values[d] if d < len(x_values) else None}
                row.update(label_map)
                row["totals"] = value
                rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------
def extract_event(event_config, group_by_fields, fecha_corte, dias_ventana=12, dias_bucket=3):
    n_buckets = dias_ventana // dias_bucket
    buckets = []
    for b in range(n_buckets):
        end_offset = (n_buckets - 1 - b) * dias_bucket
        start_offset = end_offset + (dias_bucket - 1)
        bucket_start = fecha_corte - timedelta(days=start_offset)
        bucket_end = fecha_corte - timedelta(days=end_offset)
        buckets.append((bucket_start, bucket_end))

    print(f"  event_type: {event_config['event_type']}")

    all_rows = []
    for bstart, bend in buckets:
        params = {
            "e": json.dumps(event_config),
            "s": "[]",
            "start": bstart.strftime("%Y%m%d"),
            "end": bend.strftime("%Y%m%d"),
            "m": "totals",
            "i": 1,
            "limit": 10000,
        }
        resp = requests.get(ENDPOINT, params=params, headers=AUTH_HEADER, timeout=120)
        if not resp.ok:
            print(f"  ERROR {resp.status_code} en bucket {bstart} -> {bend}")
            print(f"  Body: {resp.text[:1000]}")
            resp.raise_for_status()

        payload = resp.json()
        n_series = len(payload.get("data", {}).get("series", []))
        rows = parse_amplitude_response(payload, group_by_fields)
        warn = "  <-- WARNING: cerca del limite de 10k" if n_series >= 9000 else ""
        print(f"  {bstart} -> {bend}: {n_series} series, {len(rows)} filas{warn}")
        all_rows.extend(rows)

    return pd.DataFrame(all_rows)


# ---------------------------------------------------------------------------
# Query specs
# ---------------------------------------------------------------------------
EVENT_PRODUCT = {
    "event_type": "Product Updated",
    "filters": FILTROS_BASE,
    "group_by": [
        {"type": "event", "value": "lead_id"},
        {"type": "event", "value": "sales_channel"},
        {"type": "event", "value": "brand"},
        {"type": "event", "value": "model"},
        {"type": "event", "value": "color"},
        {"type": "event", "value": "price_net"},
    ],
}

EVENT_PERSONAL = {
    "event_type": "Personal Information Completed",
    "filters": FILTROS_BASE,
    "group_by": [
        {"type": "event", "value": "lead_id"},
        {"type": "event", "value": "dni"},
    ],
}

EVENT_DEAL_CREATED = {
    "event_type": "Deal Created",
    "filters": FILTROS_BASE,
    "group_by": [
        {"type": "event", "value": "lead_id"},
        {"type": "event", "value": "deal_id"},
    ],
}

EVENT_DEAL_ASSIGNED = {
    "event_type": "Deal Assigned",
    "filters": FILTROS_BASE,
    "group_by": [
        {"type": "event", "value": "deal_id"},
        {"type": "event", "value": "resolution"},
    ],
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"Credenciales: {API_KEY[:6]}...  |  fecha_corte: {FECHA_CORTE}  |  ventana: {DIAS_VENTANA} dias\n")

    print("[1/3] Extrayendo Product Updated...")
    gb = [g["value"] for g in EVENT_PRODUCT["group_by"]]
    df_product = extract_event(EVENT_PRODUCT, gb, FECHA_CORTE, DIAS_VENTANA, DIAS_BUCKET)
    print(f"  Total: {len(df_product)} filas | lead_id unicos: {df_product['lead_id'].nunique() if 'lead_id' in df_product.columns else 'n/a'}\n")

    print("[2/3] Extrayendo Personal info completed...")
    gb = [g["value"] for g in EVENT_PERSONAL["group_by"]]
    df_personal = extract_event(EVENT_PERSONAL, gb, FECHA_CORTE, DIAS_VENTANA, DIAS_BUCKET)
    print(f"  Total: {len(df_personal)} filas | lead_id unicos: {df_personal['lead_id'].nunique() if 'lead_id' in df_personal.columns else 'n/a'}\n")

    print("[3/4] Extrayendo Deal Created (puente lead_id <-> deal_id)...")
    gb = [g["value"] for g in EVENT_DEAL_CREATED["group_by"]]
    df_deal_created = extract_event(EVENT_DEAL_CREATED, gb, FECHA_CORTE, DIAS_VENTANA, DIAS_BUCKET)
    print(f"  Total: {len(df_deal_created)} filas | lead_id unicos: {df_deal_created['lead_id'].nunique() if 'lead_id' in df_deal_created.columns else 'n/a'}\n")

    print("[4/4] Extrayendo Deal Assigned (resolution)...")
    gb = [g["value"] for g in EVENT_DEAL_ASSIGNED["group_by"]]
    df_deal_assigned = extract_event(EVENT_DEAL_ASSIGNED, gb, FECHA_CORTE, DIAS_VENTANA, DIAS_BUCKET)
    print(f"  Total: {len(df_deal_assigned)} filas | deal_id unicos: {df_deal_assigned['deal_id'].nunique() if 'deal_id' in df_deal_assigned.columns else 'n/a'}\n")

    # ---------- Merge ----------
    print("Merge...")

    personal_cols = [c for c in ["lead_id", "dni"] if c in df_personal.columns]
    personal_dedup = (
        df_personal[personal_cols]
        .dropna(subset=["lead_id"])
        .drop_duplicates(subset=["lead_id"], keep="first")
    )

    # Puente: lead_id -> deal_id
    dc_cols = [c for c in ["lead_id", "deal_id"] if c in df_deal_created.columns]
    deal_created_dedup = (
        df_deal_created[dc_cols]
        .dropna(subset=["lead_id"])
        .drop_duplicates(subset=["lead_id"], keep="first")
    )

    # Resolution por deal_id
    da_cols = [c for c in ["deal_id", "resolution"] if c in df_deal_assigned.columns]
    deal_assigned_dedup = (
        df_deal_assigned[da_cols]
        .dropna(subset=["deal_id"])
        .drop_duplicates(subset=["deal_id"], keep="first")
    )

    df_final = (
        df_product
        .merge(personal_dedup, on="lead_id", how="left")
        .merge(deal_created_dedup, on="lead_id", how="left")
        .merge(deal_assigned_dedup, on="deal_id", how="left")
    )

    print(f"  Filas finales: {len(df_final)}")
    print(f"  Con dni: {df_final['dni'].notna().sum() if 'dni' in df_final.columns else 0}")
    print(f"  Con deal_id: {df_final['deal_id'].notna().sum() if 'deal_id' in df_final.columns else 0}")
    print(f"  Con resolution: {df_final['resolution'].notna().sum() if 'resolution' in df_final.columns else 0}")

    if "resolution" in df_final.columns:
        print("\nDistribucion de resolution:")
        print(df_final["resolution"].value_counts(dropna=False))

    # ---------- Export ----------
    out_path = os.path.join(os.path.dirname(__file__), f"cotizaciones_{FECHA_CORTE.strftime('%Y%m%d')}.csv")
    df_final.to_csv(out_path, index=False)
    print(f"\nGuardado: {out_path}")


if __name__ == "__main__":
    main()
