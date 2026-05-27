# Eventos de Amplitude usados en la extracción

Resumen sencillo de qué representa cada uno de los dos eventos que el pipeline consume desde Amplitude y cómo se relacionan entre sí.

---

## 1. `Product Updated` — Cotizaciones

Este evento se dispara cuando se genera una **cotización** en el flujo de venta.

- Es el punto donde se **crea el `lead_id`**.
- Aporta los datos del producto cotizado: `brand`, `model`, `year`, `color`, `price_net`, `store_name`, etc.
- Solo se consideran cotizaciones con **`sales_channel = dealer`** y **`country_code = MX`** — el resto se filtra fuera en la propia query a Amplitude.
- Una cotización **puede terminar en factura o no**. A nivel de este evento, son solo cotizaciones — no implica venta confirmada.

En otras palabras: cada fila de `Product Updated` representa "alguien pidió precio por este producto".

---

## 2. `Deal Created` — Apertura del deal

Este evento ocurre más adelante en el funnel, cuando la cotización se convierte en un **deal** (negociación formal con el cliente).

- Reutiliza el **`lead_id`** generado antes en `Product Updated` — ese es el campo que une ambos eventos.
- Genera dos identificadores nuevos:
  - **`deal_id`** — identificador único del deal.
  - **`dni`** — documento de identidad del cliente.

El `deal_id` es la llave que otros eventos posteriores (no usados en este pipeline) utilizan para marcar si el cliente finalmente **convirtió / ganó la venta** — es decir, si compró o no.

---

## Cómo se cruzan en el pipeline

```
Product Updated  ──(lead_id)──►  Deal Created
   (cotización)                    (deal + cliente)
```

El pipeline extrae ambos eventos por separado y los une por `lead_id`:

- De `Product Updated` toma el detalle del producto cotizado.
- De `Deal Created` agrega `deal_id` y `dni` cuando existen.

Si una cotización nunca llegó a `Deal Created`, queda registrada solo con sus datos de producto y sin `deal_id` / `dni`.
