## Eventos de amplitude

### Product updated
Evento que recopila la información referente al modelo. Puede incluir:
- **lead_id: (generado en el evento de Lead Created)**
- **sales_channel**
- brand
- model
- color
- price_net

## User properties
Recopila información relacionada con el usuario. Puede incluir:
- dni: cédula del cliente

**También se le junta el lead_id**

## Deal created - No necesario, ir a Deal Assigned ya que tiene la info de acá y más
Información varia del trato. Puede incluir:
- deal_id
- sales_channel (viene de Product Updated)

**También se le junta el lead_id**

## Deal Assigned
Datos finales si fue aprobado o no. Puede incluir:
- deal_id
- sales_channel
- resolution

Adicional:
- hs_deal_id: hubspot?


Tener una tabla que tenga toda esta información cruzada:

- lead_id (Event: Product Updated - Se crea desde Lead Created y se junta en User Properties también, en vez de tomarlo de Product Updated, se podría tomar del User Properties)
- deal_id (Event: Deal Assigned - se puede traer por el lead_id de Product Updated o User Properties)
- dni (Event: User Properties)
- sales_channel (Event: Product Updated)
- brand (Event: Product Updated)
- model (Event: Product Updated)
- color (Event: Product Updated)
- price_net (Event: Product Updated)
- resolution (Event: Deal Assigned)


Final

- dni: (Event: User properties)
- deal_id: (Event: User properties)
- sales_channel (Event: User properties)
- lead_id: (Event: User properties - aparece también en el evento Product Updated. Clave para juntar brand, model, color y price_net)
- brand (Event: Product Updated)
- model (Event: Product Updated)
- color (Event: Product Updated)
- price_net (Event: Product Updated)
- resolution (Event: Deal Assigned - tiene deal_id que viene de User Properties)