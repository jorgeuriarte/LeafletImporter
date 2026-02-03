# Problema de Indexación en Leaflet.pub

## Fecha: 3 de Febrero de 2026

## Estado: ✅ RESUELTO

## Resumen del Problema

Los posts creados con LeafletImporter (vía API directa al PDS) **se crean correctamente** pero **no aparecen en la home del blog** ni en el RSS. Sin embargo, posts creados desde el dashboard de Leaflet.pub SÍ aparecen inmediatamente.

**Dato clave**: El 14 de enero los posts del importer SÍ aparecían. Algo cambió desde entonces.

## Solución Implementada

**Causa raíz**: Leaflet está migrando de `pub.leaflet.*` a `site.standard.*` (especificación de https://standard.site). El firehose ahora solo indexa correctamente el nuevo formato.

**Fix**: Actualizado LeafletImporter para usar `site.standard.document` en lugar de `pub.leaflet.document`.

Cambios clave:
- `$type`: `pub.leaflet.document` → `site.standard.document`
- `publication` → `site` (con formato `site.standard.publication`)
- `pages` → envuelto en objeto `content`
- Eliminado campo `author` (no requerido en nuevo formato)

---

## Causa Raíz

Leaflet.pub usa un sistema de **doble escritura**:

```
┌─────────────────────────────────────────────────────────┐
│              DASHBOARD DE LEAFLET                        │
│                                                          │
│  Cuando creas un post desde leaflet.pub:                │
│                                                          │
│  1. putRecord() → PDS del usuario         ✅            │
│  2. supabase.upsert("documents")          ✅ ← CLAVE    │
│  3. supabase.upsert("documents_in_pubs")  ✅ ← CLAVE    │
│                                                          │
│  El post aparece INMEDIATAMENTE en la home              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              LEAFLET IMPORTER (nuestro)                  │
│                                                          │
│  Cuando creamos un post vía API:                        │
│                                                          │
│  1. putRecord() → PDS del usuario         ✅            │
│  2. ??? depende del firehose ???          ❌            │
│                                                          │
│  El post NO aparece en la home                          │
└─────────────────────────────────────────────────────────┘
```

### ¿Qué es el Firehose?

Leaflet tiene un servicio (`appview`) que escucha el "firehose" de Bluesky (un stream de todos los eventos de AT Protocol). Cuando detecta un nuevo `pub.leaflet.document`, lo inserta en Supabase.

**El problema**: Este firehose no es confiable al 100%. Puede:
- Tener retrasos de minutos/horas
- Perder eventos si se reinicia
- Fallar silenciosamente si el record no pasa validación

---

## Arquitectura de Leaflet

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Browser    │────▶│  Leaflet.pub │────▶│   Supabase   │
│  (Dashboard) │     │   (Next.js)  │     │  (Database)  │
└──────────────┘     └──────────────┘     └──────────────┘
       │                                         ▲
       │                                         │
       ▼                                         │
┌──────────────┐                          ┌──────────────┐
│     PDS      │◀─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ▶│   Appview    │
│  (atproto)   │      Firehose            │  (Indexer)   │
└──────────────┘      (unreliable)        └──────────────┘

El dashboard escribe a AMBOS (PDS + Supabase).
El importer solo escribe al PDS y espera que el firehose indexe.
```

---

## ¿Por qué funcionaba el 14 de enero?

Cambios relevantes después del 14 de enero:

| Fecha | Cambio |
|-------|--------|
| 21 ene | Gran refactor con nuevo schema `site.standard.document` |
| 25 ene | Cambio de ordenamiento de `indexed_at` a `sort_date` |
| 25 ene | Nueva columna calculada `sort_date` en Supabase |

Es probable que el appview (firehose consumer) se reiniciara después del 14 de enero y perdiera eventos, o que haya algún problema de validación con el nuevo schema.

---

## Opciones para Solucionar

### Opción A: Contactar a Leaflet.pub (RECOMENDADA)

Escribirles explicando el problema y pedirles:
1. Un endpoint público para indexar documentos creados vía API
2. O que revisen por qué el firehose no está indexando

**Contacto**: Probablemente en su Discord o GitHub.

### Opción B: Escribir directamente a Supabase

Hacer lo mismo que el dashboard: escribir al PDS Y a Supabase.

**Problema**: Necesitaríamos la `service_role_key` de Supabase de Leaflet, que es privada.

```javascript
// Esto es lo que hace el dashboard (publishToPublication.ts)
// Pero NO tenemos acceso a supabaseServerClient

await supabaseServerClient.from("documents").upsert({
  uri: result.uri,
  data: record,
});

await supabaseServerClient.from("documents_in_publications").upsert({
  publication: publication_uri,
  document: result.uri,
});
```

### Opción C: Re-disparar el Firehose (Workaround)

Hacer un `putRecord` de UPDATE al mismo documento para generar un nuevo evento en el firehose.

**Cómo funciona**:
```javascript
// Ya tenemos esto en el importer:
const result = await createRecord(session, 'pub.leaflet.document', rkey, record);

// NUEVO: Esperar y volver a escribir el mismo record
await new Promise(r => setTimeout(r, 5000)); // Esperar 5 segundos

// Esto genera un evento "update" en el firehose
await createRecord(session, 'pub.leaflet.document', rkey, record);
```

**Autenticación**: Usamos la misma que ya tenemos (session de Bluesky con App Password).

**API**: Es el mismo `putRecord` que ya usamos, solo que lo llamamos dos veces.

### Opción D: Esperar

El firehose puede tener retrasos de minutos a horas. Los posts podrían aparecer eventualmente.

---

## Prueba Rápida: Verificar si el Firehose Funciona

Podemos probar la Opción C manualmente:

1. **Ir a leafletimporter.pages.dev**
2. **Autenticarse**
3. **Seleccionar la publicación "Migration test"**
4. **Crear un post de prueba**
5. **Esperar 1 minuto**
6. **Crear OTRO post de prueba con el mismo botón** (esto debería funcionar igual)
7. **Verificar si el segundo aparece en la home**

Si el segundo tampoco aparece, el problema es más profundo que solo timing.

---

## Código Relevante en Leaflet

### Dashboard (publishToPublication.ts, líneas 270-297)

```typescript
// 1. Escribe al PDS
let { data: result } = await agent.com.atproto.repo.putRecord({
  rkey,
  repo: credentialSession.did!,
  collection: record.$type,
  record,
  validate: false,
});

// 2. INMEDIATAMENTE escribe a Supabase
await supabaseServerClient.from("documents").upsert({
  uri: result.uri,
  data: record as unknown as Json,
});

if (publication_uri) {
  await supabaseServerClient.from("documents_in_publications").upsert({
    publication: publication_uri,
    document: result.uri,
  });
}
```

### Firehose Consumer (appview/index.ts)

```typescript
let firehose = new Firehose({
  service: "wss://relay1.us-west.bsky.network",
  filterCollections: [
    "pub.leaflet.document",
    "pub.leaflet.publication",
    // ...
  ],
  handleEvent: async (evt) => {
    if (evt.collection === "pub.leaflet.document") {
      // Valida el record (puede fallar silenciosamente)
      let record = PubLeafletDocument.validateRecord(evt.record);
      if (!record.success) {
        console.log(record.error);
        return; // SILENTLY DROPS THE RECORD
      }

      // Inserta en Supabase
      await supabase.from("documents").upsert({
        uri: evt.uri.toString(),
        data: record.value,
      });
    }
  }
});
```

---

## Próximos Pasos Recomendados

1. **Corto plazo**: Contactar a Leaflet.pub para entender el problema
2. **Mientras tanto**: Probar la Opción C (re-disparar firehose) para ver si funciona
3. **Largo plazo**: Si Leaflet no expone un endpoint, considerar usar el dashboard vía Playwright (automatizar la UI)

---

## Referencias

- Repositorio de Leaflet: https://tangled.org/leaflet.pub/leaflet
- Commits relevantes:
  - `1626669b` (25 ene) - Add sort_date column
  - `1c680551` (25 ene) - Use sort_date to order
  - `69de7db6` (21 ene) - Refactor standard.site
