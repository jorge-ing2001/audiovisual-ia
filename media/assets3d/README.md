# Assets 3D del MVP: catalogo, origen y licencias

Esta carpeta es el **punto de colocacion** de los assets 3D que consume el motor 4
(Blender). El codigo que resuelve estas rutas es
[`backend/app/engines/engine3d/assets.py`](../../backend/app/engines/engine3d/assets.py);
los pasos para descargarlos y producir el video estan en
[`docs/ENGINE-3D-RUNBOOK.md`](../../docs/ENGINE-3D-RUNBOOK.md).

> **Los assets 3D de terceros NO se versionan en este repositorio.** Solo se versiona este
> `README.md`. Los binarios (`.blend`, `.fbx`, texturas, etc.) los descargas tu y los
> colocas en `AIV_BLENDER_ASSETS_DIR` (si lo dejas vacio, se usa esta misma carpeta
> `media/assets3d/`). Ver `.gitignore`: `media/` esta ignorado salvo una excepcion
> explicita para este fichero.

---

## 1. Estructura de carpetas esperada

`assets.py` busca los ficheros con estos nombres e ids **estables** (no los cambies: los
usan `assets.py` y `mapping.py`):

```
$AIV_BLENDER_ASSETS_DIR/           (o media/assets3d/ si la variable esta vacia)
  characters/
    hero.blend
  animations/
    idle.fbx
    walk.fbx
    turn_head.fbx
    point.fbx
    react.fbx
  sets/
    room_interior.blend
    outdoor_street.blend
```

---

## 2. Catalogo del MVP: origen y licencia por asset

### 2.1 Personaje (identidad constante)

| Id / fichero | Origen | Licencia / terminos |
|--------------|--------|---------------------|
| `characters/hero.blend` | **Mixamo** (Adobe) | Uso libre con **cuenta Adobe**; sujeto a los [terminos de uso de Mixamo](https://helpx.adobe.com/creative-cloud/faq/mixamo-faq.html). Personajes y animaciones se pueden usar en proyectos propios; **no redistribuir** los ficheros de origen como tales. Por eso NO se versionan en el repo. |

Es el **unico** personaje del MVP: el mismo mesh/rig/texturas en todas las escenas, lo que
garantiza identidad visual constante por construccion.

### 2.2 Animaciones corporales

| Id / fichero | Origen | Licencia / terminos |
|--------------|--------|---------------------|
| `animations/idle.fbx` | **Mixamo** (Adobe) | Mixamo: cuenta Adobe, no redistribuir (ver arriba). |
| `animations/walk.fbx` | **Mixamo** (Adobe) | idem |
| `animations/turn_head.fbx` | **Mixamo** (Adobe) | idem |
| `animations/point.fbx` | **Mixamo** (Adobe) | idem |
| `animations/react.fbx` | **Mixamo** (Adobe) | idem |

Rig humanoide compatible con Mixamo; los nombres los elige `mapping.py` por escena a partir
de la narracion y las `camera_notes`.

### 2.3 Sets / escenarios y props

| Id / fichero | Origen | Licencia |
|--------------|--------|----------|
| `sets/room_interior.blend` | Modelos **CC0** (p. ej. [Poly Haven](https://polyhaven.com/), [Kenney](https://kenney.nl/)) | **CC0 1.0** (dominio publico): sin atribucion obligatoria. Aun asi se registra su origen. |
| `sets/outdoor_street.blend` | Modelos **CC0** (Poly Haven / Kenney) | **CC0 1.0** (dominio publico). |

Prefiere assets **CC0** para sets y props: dominio publico, sin dudas de redistribucion. Aun
cuando CC0 no exige atribucion, el origen de cada asset queda registrado aqui y en `NOTICE`.

---

## 3. Reglas de licencia (resumen)

- **Mixamo (Adobe):** personaje y animaciones de uso libre con cuenta Adobe; revisa sus
  terminos antes de cualquier redistribucion. Para evitar dudas, estos ficheros **no se
  commitean** al repo: viven en `AIV_BLENDER_ASSETS_DIR` (como el resto de `media/`).
- **CC0:** dominio publico; preferido para sets/props. Sin atribucion obligatoria.
- **Sin marcas de agua ni logos de redes:** los assets libres elegidos no deben traerlas
  (regla dura del prompt del MVP).
- **Blender** es la herramienta de render (GPL); no se enlaza ni se redistribuye su codigo
  con este proyecto (ver `NOTICE` y
  [`docs/REFERENCE-REPOS-LICENSES.md`](../../docs/REFERENCE-REPOS-LICENSES.md)).

Si amplias el catalogo (mas personajes, sets o animaciones), anade aqui su origen y licencia
y la entrada correspondiente en `assets.py`.
