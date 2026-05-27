import json
import os
import base64
import requests
import pandas as pd
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AMPLITUDE_API_KEY"]
API_SECRET = os.environ["AMPLITUDE_API_SECRET"]

_token = base64.b64encode(f"{API_KEY}:{API_SECRET}".encode()).decode()
AUTH_HEADER = {"Authorization": f"Basic {_token}"}
ENDPOINT = "https://amplitude.com/api/2/events/segmentation"

# --- Parser parseLong: una fila por (combinacion x dia) ---
# Identico al de price_diff/app.ipynb

def _normalize_label(label, n_cols):
    """
    Convierte un label de Amplitude en una lista plana de strings,
    una por cada campo del group_by.

    Casos que hemos visto en respuestas de /events/segmentation:
      - "v1, v2, v3"               (string con coma-espacio, 1+ group_by)
      - "v1; v2; v3"               (string con punto-coma)
      - ["v1", "v2", "v3"]         (lista de strings, multi group_by)
      - ["0", "v1; v2; v3"]        (lista [idx, "valores joineados"])
      - [0, "v1; v2; v3"]          (idem con int)
    """
    if isinstance(label, list):
        # Si alguno de los elementos es un string que contiene los valores
        # joineados por "; " o ", ", expandelo.
        flat = []
        for p in label:
            if isinstance(p, str) and "; " in p:
                flat.extend(p.split("; "))
            elif isinstance(p, str) and ", " in p and len(label) < n_cols:
                flat.extend(p.split(", "))
            else:
                flat.append(str(p))
        # Caso [idx, "joined"]: el idx sobra. Si tenemos n_cols+1 partes y la primera
        # es un indice numerico, descartala.
        if len(flat) == n_cols + 1 and flat[0].isdigit():
            flat = flat[1:]
        return flat

    # label es string
    parts = label.split(", ")
    if len(parts) < n_cols and "; " in label:
        parts = label.split("; ")
    return parts


def parse_amplitude_response(resp_json, group_by_fields):
    data = resp_json.get("data", {})
    series = data.get("series", [])
    labels = data.get("seriesLabels", [])
    x_values = data.get("xValues", [])

    rows = []
    n_cols = len(group_by_fields)

    # Debug puntual: dejar ver la forma cruda del primer label
    if labels:
        print(f"  [parser] label[0] type={type(labels[0]).__name__} repr={labels[0]!r}")

    for i, serie in enumerate(series):
        label = labels[i] if i < len(labels) else ""
        parts = _normalize_label(label, n_cols)

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

# --- Helper: corre un event_config sobre N buckets de 3 dias ---

def extract_event(event_config, group_by_fields, fecha_corte, dias_ventana=12, dias_bucket=3):
    n_buckets = dias_ventana // dias_bucket
    buckets = []
    for b in range(n_buckets):
        end_offset = (n_buckets - 1 - b) * dias_bucket
        start_offset = end_offset + (dias_bucket - 1)
        bucket_start = fecha_corte - timedelta(days=start_offset)
        bucket_end = fecha_corte - timedelta(days=end_offset)
        buckets.append((bucket_start, bucket_end))

    print(f"event_type: {event_config['event_type']}")
    print(f"group_by enviado: {event_config['group_by']}")

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
            print(f"\nERROR {resp.status_code} en bucket {bstart} -> {bend}")
            print("Body:", resp.text[:1000])
            print("URL:", resp.url[:500])
            resp.raise_for_status()
        payload = resp.json()

        n_series = len(payload.get("data", {}).get("series", []))
        rows = parse_amplitude_response(payload, group_by_fields)
        warn = "  <-- WARNING: cerca del limite de 10k" if n_series >= 9000 else ""
        print(f"  {bstart} -> {bend}: {n_series} series, {len(rows)} filas{warn}")
        all_rows.extend(rows)

    return pd.DataFrame(all_rows)