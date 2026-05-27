# Notas de Amplitude

Notas personales / gotchas a tener en cuenta cuando se trabaja con Amplitude en este proyecto.
Se va alimentando a medida que aparecen temas nuevos.

---

## 1. Diferencia de timezone entre Dashboard y API

### Contexto
- El **dashboard de Amplitude** muestra los datos en timezone de **México**.
- El proyecto/cuenta en Amplitude fue creado desde **Chile**, por lo que el TZ "de origen" del proyecto es **Chile**.
- Al hacer la extraccion por API, Amplitude **no respeta el TZ visible en el dashboard**: devuelve los datos agregados al TZ de Chile (el de origen).

### Sintoma
- Los totales/conteos del API **no coinciden exactamente** con los del dashboard en MX.
- La diferencia suele ser **minima**, producto del desfase de horas entre CL y MX, que mueve algunos eventos de un dia al siguiente en los bordes.

### Validacion
- Si en el dashboard cambias manualmente el TZ a **Chile**, los numeros **coinciden** con lo que devuelve la API.
- Eso confirma que no hay bug de extraccion, solo diferencia de zona horaria.

### Decision
- Trabajamos con los datos **tal cual los entrega el API** (TZ Chile), asumiendo esa diferencia minima contra el dashboard (TZ Mexico).
- Dejar documentado para no perder tiempo persiguiendo la diferencia mas adelante.

### Pipeline `fecha_corte` y `run-pipeline` (Edge Function)
- La etiqueta **`fecha_corte`** y el rango **`end`** contra Amplitude usan el **día civil anterior** en **America/Santiago** (`BUSINESS_TIMEZONE` en env para override). El "hoy" civil de Chile **no** se incluye como corte válido ni como último día de ventana hasta que cierra ese día civil.
- Tras cambiar esa lógica en código, hay que **volver a ejecutar** `run-pipeline` contra Supabase para **regenerar** `diff_facturas`/`df_expandido`; los cortes antiguos con `fecha_corte` equivocada en BD siguen siendo historia hasta nueva corrida o borrado selectivo.

---

## 2. Por que usamos `Event Segmentation API` y no otros endpoints

Actualmente las extracciones se hacen contra:

```
GET https://amplitude.com/api/2/events/segmentation
```

### Razones
- Permite **reconstruir en codigo los mismos filtros que arma el dashboard** (event_type, filters, group_by, intervalo, metrica), sin depender de la UI.
- Devuelve una **respuesta JSON estructurada** (`data.series`, `data.seriesLabels`, `data.xValues`), facil de parsear a DataFrame y reutilizar entre charts.
- Funciona con **autenticacion basica** (`API_KEY:API_SECRET` en Base64), sin flujos OAuth ni MCP.
- Es el unico que nos permite **ejecutar la misma query con distintos rangos de fechas / filtros** de forma programatica y parametrizable.

### Por que descartamos los otros que probamos
Ubicacion de los POCs: `trash/amplitude/`.

- **`GET /api/3/chart/{chart_id}/csv`** (Dashboard REST)
  - Devuelve el export tal como se ve en el dashboard, pero el payload llega como **string CSV embebido** con titulo, tabs escapados y celdas en blanco (ver nota 3).
  - Obliga a parsear CSV manualmente y es fragil ante cambios del chart.
  - No permite cambiar filtros: estas atado a como quedo guardado el chart en la UI.

- **`GET /api/3/chart/{chart_id}/query` + `/info`** (definicion del chart)
  - Util solo para **inspeccionar** la configuracion interna (event_type, filters, group_by) de un chart ya guardado.
  - No extrae datos por si solo. Lo usamos puntualmente para reconstruir los parametros que le pasamos a `/events/segmentation`.

- **MCP de Amplitude** (`amplitude_handle.py`, JSON-RPC sobre SSE)
  - Requiere token OAuth con refresh y manejo de sesiones/streams.
  - Mas friccion para un caso simple de extraccion tabular.
  - Tiene sentido para escenarios agenticos, no para pipelines batch.

### Resumen rapido
| Endpoint                          | Uso                              | Estado en el proyecto |
|-----------------------------------|----------------------------------|-----------------------|
| `/api/2/events/segmentation`      | Extraccion principal de datos    | **En uso**            |
| `/api/3/chart/{id}/query` + `/info` | Inspeccion de config del chart | Uso puntual           |
| `/api/3/chart/{id}/csv`           | Export CSV del dashboard         | Descartado            |
| MCP (`amplitude_handle.py`)       | Acceso via agente/MCP            | Descartado            |

---

## 3. Parser del CSV embebido (referencia historica)

> Esta nota aplica al endpoint `GET /api/3/chart/{chart_id}/csv` que **ya no usamos**.
> Se mantiene por si en el futuro hay que volver a tocarlo.

### Problema detectado
Al consumir `GET /api/3/chart/{chart_id}/csv`, para algunos charts la respuesta llega en `data` como **string CSV embebido**, no como objeto JSON estructurado (`series`, `seriesLabels`, `xValues`).

Adicionalmente, ese string puede incluir:
- una primera linea de titulo (por ejemplo, `Facturacion MX (ult. sugerido)`),
- filas vacias,
- columnas con tabs escapados/reales (`\\t` y `\t`),
- estructura jerarquica con celdas vacias que requieren `forward-fill`.

### Sintomas observados
- `AttributeError: 'str' object has no attribute 'get'` al asumir que `data` era dict.
- DataFrame con una sola columna (solo el titulo de la tabla).
- Valores con `\t` visibles en headers y celdas.
- Export a CSV sin las dimensiones esperadas (al momento de esa POC: `model`, `year`, `color`, `price`, `brand`; ver receta vigente en sección 4).

### Causa raiz
El parser trataba todos los casos como JSON estructurado o TSV simple, pero el payload real del chart era CSV quoted con una fila de titulo previa al header real.

### Correccion implementada (en el POC)
1. Normalizacion de payload: soporte para `data` como `dict`, JSON-string o CSV-string.
2. Parser CSV robusto: `csv.reader`, deteccion del header real (primera fila con 2+ celdas no vacias), eliminacion solo de filas/columnas totalmente vacias, renombrado estable de columnas vacias/duplicadas.
3. Limpieza de texto: remocion de `\\t`/`\t` y saltos de linea en headers y valores.
4. Normalizacion jerarquica: `forward-fill` en columnas categoricas para conservar contexto de dimensiones.

### Checklist de validacion
- `df.shape` debe tener mas de una columna para charts con dimensiones.
- `df.columns.tolist()` debe incluir dimensiones esperadas + metrica.
- `df.head()` no debe mostrar `\t` en headers ni valores.
- `df.to_csv(..., index=False)` debe exportar todas las columnas.

---

## 4. Receta de extracción vigente (refactor-v2)

Desde 2026-05-15 el pipeline lee **cotizaciones** del marketplace en lugar de facturas finales. Amplitude sigue siendo la fuente; lo que cambia es el evento y las dimensiones.

| Item | Valor |
|---|---|
| Event | `Product Updated` |
| Filtros event | `country_code = MX`, `sales_channel = dealer` |
| Group by (8 dims) | `lead_id`, `brand`, `model`, `color`, `price_net`, `year`, `store_name`, `store_alias` |
| Ventana | 12 días: `[fecha_corte - 11, fecha_corte]` |
| Bucketing | 4 buckets contiguos de 3 días — necesario para no rebasar `limit=10000` |
| Métrica | `totals` (i = 1, intervalo diario) |

La spec de referencia técnica vive en [`docs/specs/T01-pipeline-technical-reference.md` §3.1](../specs/T01-pipeline-technical-reference.md). La narrativa del cambio (por qué cotización en lugar de factura) está en [`docs/refactor-v2/README.md`](../refactor-v2/README.md). El extractor en código: [`supabase/functions/run-pipeline/amplitude.ts`](../../supabase/functions/run-pipeline/amplitude.ts) → `getFacturasDaily` (nombre interno preservado por compatibilidad).

> El evento `Deal Scored` (RQLs) sigue intacto y se mantiene en el sistema. Se usa para el ranking de demanda (`rqls_7d`, `rank_rqls` en `tabla_macro`). Lo que se eliminó en el slice 02 del refactor-v2 fue el filtro Pareto que usaba los RQLs para seleccionar el universo de análisis, no el evento en sí.

---

## 5. "User Properties" en Amplitude NO es un evento

### Confusión común
En el detalle de un evento, Amplitude muestra un panel lateral llamado **User Properties** (City, Country, Device, Amplitude ID, etc.). Es fácil pensar que existe un evento llamado "User Properties" que se puede consultar — **no existe**.

### Qué son realmente
- Las **user properties "reales"** (City, Country, Device, etc.) son atributos del usuario seteados con `identify()` desde el SDK. No son un evento, persisten globalmente y se "pegan" a cada evento del usuario.
- Los campos que **parecen** user properties pero que el dev manda en cada `track()` (ej. `lead_id`, `dni`, `sales_channel`, `deal_id`) son **event properties**. Aparecen dentro del cuadro "Event Properties" cuando inspeccionas un evento, y se propagan entre eventos solo si el dev las incluye en cada `track()`.

### Síntoma
Si pides `event_type: "User Properties"` contra `/api/2/events/segmentation` obtienes:
```
400 Bad Request - "Invalid User Properties"
```

### Cómo extraer una user/event property
- **Event property** → `{"type": "event", "value": "lead_id"}` dentro de `group_by` del evento donde realmente aparece.
- **User property real** → `{"type": "user", "value": "city"}` dentro de `group_by` de cualquier evento del usuario.
- **Group property** → `{"type": "group", "value": "gp:dni"}` (prefijo `gp:` obligatorio).

> El mensaje de error `"Invalid user property X"` que devuelve Amplitude es **genérico**: dice "user property" incluso cuando mandaste `type: "event"` o `type: "group"`. No te dejes confundir.

---

## 6. Formato de `seriesLabels` — el parser tiene que ser robusto

Dependiendo del query, `data.seriesLabels` puede llegar en **al menos 4 formas distintas**:

| # | Forma | Cuándo aparece |
|---|---|---|
| 1 | `"v1, v2, v3"` (string, coma-espacio) | 1 group_by (o pocos) |
| 2 | `"v1; v2; v3"` (string, punto-coma) | Algunos casos con varias dimensiones |
| 3 | `["v1", "v2", "v3"]` (lista plana) | Multi group_by, algunos proyectos |
| 4 | `[0, "v1; v2; v3"]` (lista `[idx, joined]`) | Multi group_by en este proyecto |

La forma 4 es la traicionera: el primer elemento es un índice numérico (creemos que de "segment", aunque siempre mandamos `s=[]`) y el segundo es **todos los valores del group_by concatenados con `"; "`** en una sola string.

### Parser recomendado

```python
def _normalize_label(label, n_cols):
    if isinstance(label, list):
        flat = []
        for p in label:
            if isinstance(p, str) and "; " in p:
                flat.extend(p.split("; "))
            else:
                flat.append(str(p))
        # Caso [idx, "joined"]: descartar el idx si sobra una posicion
        if len(flat) == n_cols + 1 and flat[0].isdigit():
            flat = flat[1:]
        return flat
    # string
    parts = label.split(", ")
    if len(parts) < n_cols and "; " in label:
        parts = label.split("; ")
    return parts
```

Implementación viva: [`src/utils/amplitude.py`](../../src/utils/amplitude.py).

### Síntoma de un parser que no maneja la forma 4
Si ves columnas como esta:

| lead_id | sales_channel | brand | model |
|---|---|---|---|
| 0 | `6a08...; dealer; TVS; Stryker; (none); 32990` | (none) | (none) |

El `0` es el índice y todo lo demás cayó en la siguiente columna porque el parser asumió que el label ya venía como lista plana.

---

## 7. `Deal Assigned` no tiene `lead_id` — usar `Deal Created` como puente

### Descubrimiento
Al armar la query de cotizaciones, asumimos que `Deal Assigned` (donde está la `resolution` final del deal: approved/rejected/preApproved) traía `lead_id` propagado. **No lo trae.** Sus event properties son: `deal_id`, `resolution`, `country_code`, `sales_channel`, `executive_*`, `hs_deal_id`, `score`. Nada para amarrar con la cotización original.

### Solución
Usar `Deal Created` como **puente** porque sí tiene ambos: `lead_id` (del flujo previo) y `deal_id` (recién generado en ese evento). Esto agrega una query más pero es la única manera de cruzar la `resolution` con el `lead_id` de `Product Updated`.

```
Product Updated  ──(lead_id)──►  Deal Created  ──(deal_id)──►  Deal Assigned
  (cotizacion)                  (puente)                       (resolution)
```

### Cómo verificarlo antes de codificar
En el dashboard de Amplitude, abre el detalle de un evento `Deal Assigned` cualquiera y mira el panel "Event Properties". Si no aparece `lead_id` ahí, no lo vas a poder pedir por API por más que insistas con `type: "event"` o `type: "user"`.
