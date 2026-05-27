# Eventos de Amplitude usados en la extracción de cotizaciones

Mapeo de los **4 eventos** que el extractor de cotizaciones consume desde Amplitude y cómo se cruzan entre sí para construir una fila por cotización con `dni` + `resolution`.

Implementación: [`notebooks/cotizaciones/app.py`](../../notebooks/cotizaciones/app.py) (y su notebook gemelo).

---

## Resumen visual del funnel

```
Checkout Started  ─►  Personal Info Completed  ─►  Product Updated  ─►  Deal Created  ─►  Deal Assigned
   sales_channel       lead_id (verde)             brand, model,         deal_id            resolution
                       dni (verde)                 color, price_net      (verde)            (verde)
```

> "Verde" = la propiedad se **crea** en ese evento (antes no existía). En eventos posteriores se propaga si el dev la incluye en el `track()`, pero no siempre lo hace.

---

## 1. `Product Updated` — Cotizaciones

Cada vez que el usuario configura/actualiza el producto cotizado.

**Event properties que usamos:** `lead_id`, `sales_channel`, `brand`, `model`, `color`, `price_net`.

**Filtros aplicados en la query:** `country_code = MX`, `sales_channel = dealer`.

**Grano:** un mismo `lead_id` puede tener **varios** `Product Updated` (el usuario cambia de modelo, color, etc.). Los conservamos todos — la `resolution` final desambigua cuál cotización terminó aprobada.

---

## 2. `Personal Information Completed` — DNI

Se dispara cuando el usuario completa el formulario de datos personales.

**Event properties que usamos:** `lead_id`, `dni`.

**Por qué este evento y no otro:** `dni` se **crea** acá por primera vez. En eventos posteriores como `Product Updated` o `Deal Created` no aparece (el dev no lo propaga).

> ⚠️ El nombre del evento es **`Personal Information Completed`** (con "I" mayúscula y "Information" completo). Variantes como `Personal info completed` devuelven `400 "Invalid Personal info completed"`. Verifica el casing exacto en el dashboard de Amplitude antes de codificar.

---

## 3. `Deal Created` — Puente lead_id ↔ deal_id

Apertura formal del deal.

**Event properties que usamos:** `lead_id`, `deal_id`.

**Rol en el pipeline:** **puente**. Es el único evento donde aparecen `lead_id` y `deal_id` juntos. Sin él, no podríamos amarrar la `resolution` (que vive en `Deal Assigned`) con la cotización original (que se identifica por `lead_id`).

> Otros campos disponibles en este evento (no los usamos hoy, pero quedan documentados): `amount`, `currency_code`, `dealer_email`, `dealer_tax_id`, `down_payment`, `offering_version`.

---

## 4. `Deal Assigned` — Resolution

Asignación final del deal y veredicto.

**Event properties que usamos:** `deal_id`, `resolution`.

**Valores de `resolution` observados:** `approved`, `rejected`, `preApproved`.

**Gotcha importante:** `Deal Assigned` **NO tiene `lead_id`**. Sus propiedades son `deal_id`, `resolution`, `executive_*`, `hs_deal_id`, `score`, etc. Por eso necesitamos el paso 3 (`Deal Created`) como puente. Detalle técnico en [`docs/amplitude/amplitude.md §7`](../amplitude/amplitude.md).

---

## Cómo se cruzan en el pipeline

```
Product Updated                Personal Information Completed
   │                              │
   │ lead_id ◄──── join ────► lead_id
   │                              │ dni
   ▼                              ▼
   ──────────── df merged ──────────
                  │
                  │ lead_id ◄──── join ───► Deal Created (lead_id, deal_id)
                  ▼
                  ──── df con deal_id ────
                            │
                            │ deal_id ◄──── join ───► Deal Assigned (deal_id, resolution)
                            ▼
                       df_final
                 (1 fila por cotización con
                  dni y resolution opcionales)
```

**Llaves de join:**
- `lead_id` une **Product Updated** ↔ **Personal Information Completed** ↔ **Deal Created**
- `deal_id` une **Deal Created** ↔ **Deal Assigned**

**Dedup aplicado en cada lado:**
- Personal Information Completed: `keep="first"` por `lead_id` (1 dni por lead).
- Deal Created: `keep="first"` por `lead_id` (asumimos 1 deal por lead — revisar si aparecen casos con 2).
- Deal Assigned: `keep="first"` por `deal_id`. **Pendiente:** priorizar `resolution = "approved"` si hay varias asignaciones por deal.

---

## Cobertura esperada

No todas las cotizaciones llegan al final del funnel:

| Etapa | Filas esperadas |
|---|---|
| Cotizaciones (Product Updated) | 100% (universo) |
| Con `dni` (Personal Info Completed) | ~93% típico — algunas cotizaciones no completan datos personales |
| Con `deal_id` (Deal Created) | menor — solo las que avanzan a deal |
| Con `resolution` (Deal Assigned) | aún menor — solo las que recibieron veredicto |

Si ves `Con deal_id: 0` o `Con resolution: 0` en el merge final, el problema más probable es que el nombre del evento o de la prop esté mal escrito en la query, no que no haya datos.

---

## Filtros y ventana

| Item | Valor |
|---|---|
| Filtros (todos los eventos) | `country_code = MX`, `sales_channel = dealer` |
| Ventana | 12 días: `[fecha_corte - 11, fecha_corte]` |
| Bucketing | 4 buckets de 3 días — necesario para no rebasar `limit=10000` series por query |
| Métrica | `totals` (i=1, intervalo diario) |
| Endpoint | `GET /api/2/events/segmentation` con Basic Auth |
