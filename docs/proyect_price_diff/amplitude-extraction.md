# Extracción de datos desde Amplitude

Referencia técnica rápida sobre cómo el pipeline obtiene datos de Amplitude, qué rango de fechas cubre y qué queda guardado en Supabase.

---

## Resumen

El pipeline corre una vez al día (cron nocturno). Su primera tarea es extraer tres tipos de datos desde Amplitude y guardarlos en Supabase. Todo lo demás —cálculo de precios, rankings, tabla macro— se construye sobre esa data extraída.

---

## Fuentes de datos

El pipeline hace tres consultas independientes a la API de Amplitude:

| Extractor | Evento Amplitude | Canal | País | Ventana | Qué extrae |
|---|---|---|---|---|---|
| **Facturas** | `Product Updated` | `dealer` | MX | 12 días | brand, model, year, color, price_net, store_name, lead_id |
| **Deals** | `Deal Created` | `dealer` | MX | 12 días | lead_id, deal_id, dni |
| **RQLs** | `Deal Scored` | `online` | MX | 7 días | publication_code, year, color, amount (solo aprobados) |

> **RQLs** filtra adicionalmente `resolution = approved | preApproved`.

---

## Cómo se maneja el rango de fechas

### Ventana de 12 días (Facturas y Deals)

La API de Amplitude tiene un límite de **10,000 series por query**. Para no rebasarlo, la ventana de 12 días se divide en **4 buckets contiguos de 3 días**:

```
Bucket 1: fecha_corte - 11  →  fecha_corte - 9
Bucket 2: fecha_corte - 8   →  fecha_corte - 6
Bucket 3: fecha_corte - 5   →  fecha_corte - 3
Bucket 4: fecha_corte - 2   →  fecha_corte
```

Se hace una llamada por bucket y los resultados se concatenan. Si algún bucket devuelve ≥ 9,000 series, se registra una alerta en los logs (pero no hay reintento automático).

### Ventana de 7 días (RQLs)

Se hace en una sola llamada. No necesita subdividirse porque el volumen de deals aprobados es menor.

### Deduplicación de Deals

Como un mismo `lead_id` puede aparecer en múltiples buckets, los deals se deduplicán por `lead_id` conservando la entrada que tenga `deal_id` y `dni` más completos.

---

## Qué queda en Supabase

Cada ejecución del pipeline guarda (y reemplaza) los datos del día en estas tablas:

| Tabla | Campo de fecha | Qué contiene |
|---|---|---|
| `df_expandido` | `fecha_ingesta` | Una fila por factura de los últimos 12 días, enriquecida con `deal_id` y `dni` |
| `df_sin_code` | `fecha_corte` | Facturas que no cruzaron con el catálogo de SKUs |
| `tabla_macro` | `fecha_corte` | Resumen por SKU: precio típico, diferencia vs marketplace, ranking RQL |
| `inventario_snapshot_diario` | `snapshot_date` | Snapshot del inventario activo del día |
| `pipeline_runs` | `fecha_corte` | Métricas del run (cuántas facturas, cuántos SKUs, etc.) |

> El frontend siempre consulta por la **última `fecha_ingesta` / `fecha_corte` disponible**, no por un rango.

---

## Cómo se hace la extracción por API

### Endpoint

```
GET https://amplitude.com/api/2/events/segmentation
```

Autenticación: **HTTP Basic Auth** (`apiKey:apiSecret` en Base64).

---

### Estructura del query (parámetros GET)

| Parámetro | Tipo | Descripción |
|---|---|---|
| `e` | JSON string | Evento, filtros y `group_by` |
| `s` | JSON string | Segmentos — siempre `[]` |
| `start` | `YYYYMMDD` | Inicio de la ventana (sin guiones) |
| `end` | `YYYYMMDD` | Fin de la ventana (sin guiones) |
| `m` | string | Métrica — siempre `"totals"` |
| `i` | number | Intervalo en días: `1` (diario) o `7` (semanal) |
| `limit` | number | Máx. series — siempre `10000` |

El parámetro `e` tiene la forma:

```json
{
  "event_type": "Product Updated",
  "filters": [
    { "subprop_type": "event", "subprop_key": "country_code",  "subprop_op": "is", "subprop_value": ["MX"] },
    { "subprop_type": "event", "subprop_key": "sales_channel", "subprop_op": "is", "subprop_value": ["dealer"] }
  ],
  "group_by": [
    { "type": "event", "value": "lead_id" },
    { "type": "event", "value": "brand" },
    { "type": "event", "value": "model" },
    ...
  ]
}
```

> **Propiedad de grupo (`gp:`)** — `dni` es una *group property* en Amplitude y se debe pedir con el prefijo `gp:dni` en `group_by`. Sin el prefijo la API devuelve `400 "Invalid user property dni"`. En la respuesta se mapea de vuelta al nombre corto `"dni"`.

---

### Respuesta de la API

```json
{
  "data": {
    "series":       [[3], [1], ...],          // valores por día/semana, uno por combinación
    "seriesLabels": ["lead-1, Toyota, Yaris", ...],  // label de cada combinación
    "xValues":      ["2026-05-15", "2026-05-16", ...]  // fechas del eje X
  }
}
```

Cada elemento de `series[i]` es un array con un valor por fecha en `xValues`. El `label` de cada serie concatena los campos del `group_by` separados por `", "` (o `"; "` en algunos casos — el parser lo normaliza).

---

### Cómo se parsea la respuesta

**Modo `parseLong`** (Facturas) — una fila por *(combinación × día)*:

```
series[i][d] > 0  →  { date: xValues[d], lead_id: ..., brand: ..., ..., totals: value }
```

**Modo `parseTotals`** (RQLs, Deals) — una fila por *combinación*, sumando todos los días:

```
sum(series[i]) > 0  →  { lead_id: ..., deal_id: ..., ..., totals: sum }
```

El label se divide por `", "` y se asigna por posición a cada columna del `group_by` — **el orden del array `group_by` es el orden exacto de las columnas**. Si el label tiene menos partes que columnas, las sobrantes reciben `"(none)"`.

---

### Ejemplo de llamada real — Facturas, bucket 3 días

```
GET https://amplitude.com/api/2/events/segmentation
  ?e={"event_type":"Product Updated","filters":[{"subprop_type":"event","subprop_key":"country_code","subprop_op":"is","subprop_value":["MX"]},{"subprop_type":"event","subprop_key":"sales_channel","subprop_op":"is","subprop_value":["dealer"]}],"group_by":[{"type":"event","value":"lead_id"},{"type":"event","value":"brand"},{"type":"event","value":"model"},{"type":"event","value":"color"},{"type":"event","value":"price_net"},{"type":"event","value":"year"},{"type":"event","value":"store_name"},{"type":"event","value":"store_alias"}]}
  &s=[]
  &start=20260514
  &end=20260516
  &m=totals
  &i=1
  &limit=10000
Authorization: Basic <base64(apiKey:apiSecret)>
```

---

### Variante semanal — RQLs históricos

Para extraer RQLs sobre un rango largo (auditoría) se usa `i=7`. Amplitude devuelve una entrada en `xValues` por semana (inicio del período), lo que permite barrer 8–12 semanas en una sola llamada.

---

## Limitaciones conocidas

| Limitación | Detalle |
|---|---|
| **Ventana fija de 12 días** | Facturas con `invoice_date` anterior a `fecha_corte - 11` no aparecen en el snapshot actual. Para encontrarlas hay que buscar en snapshots anteriores. |
| **Límite de 10,000 series por bucket** | Si en un período de 3 días hay más de 10,000 combinaciones únicas, Amplitude trunca silenciosamente. El pipeline lo detecta y avisa en los logs, pero no hay reintento. |
| **Sin paginación en Amplitude** | La API no pagina resultados. El único mecanismo de mitigación son los buckets de 3 días. |
| **Timezone** | Las fechas en Amplitude están en `America/Santiago` (TZ del proyecto original), no en `America/Mexico_City`. Puede haber diferencias de ±1 día en bordes de jornada. |
