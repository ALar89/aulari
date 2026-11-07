# CLAUDE.md — Aulari

App de escritorio en Python que descarga el material de un Moodle (por defecto, el Campus Virtual de la UAB, "e-aules"), lo ordena en carpetas y exporta el calendario.

**Antes de implementar cualquier funcionalidad, leer la sección correspondiente de `docs/design.md`** (requisitos, modelo de datos, ADR, riesgos). Este archivo solo contiene las reglas de trabajo.

## 1. Estado

- **Fase 1 terminada** (confirmada por el usuario el 2026-11-06). Resultados en `docs/design.md` §10; scripts en `poc/`.
- **Fase 2 terminada** (confirmada por el usuario el 2026-11-06): núcleo sin interfaz en `src/aulari/`, verificado con una sincronización real de una asignatura (`poc/sync_one_course.py`).
- **Fase 3 terminada** (confirmada por el usuario el 2026-11-06): `calendar_export.py`, verificado con Proton Calendar (sin duplicados al reimportar).
- **Fase 4 terminada** (confirmada por el usuario el 2026-11-06): interfaz Tkinter en `src/aulari/ui/` (`python -m aulari`), login automático con `moodlemobile://`, sincronización con calendario, alias, «Recuperar borrados».
- **Fase 5 cerrada** (confirmada por el usuario el 2026-11-07): nombre «Aulari» (D5), ampliación RF13–RF19, README en inglés y castellano, licencia MIT, dependencias con hashes, ejecutable PyInstaller verificado, CI en verde y flujo de versiones en GitHub Actions. Repositorio: https://github.com/ALar89/aulari.
- Siguiente paso: hacer público el repositorio y publicar `v0.1.0` (etiqueta → compilación en GitHub con atestación). Después, solicitar la firma a SignPath. Más adelante: sincronización automática e idiomas catalán e inglés.
- Hay **decisiones pendientes** en `docs/design.md` §1. Claude no las toma por su cuenta: las plantea al usuario.
- No empezar una fase hasta que la anterior funcione y el usuario lo confirme.

## 2. Reglas de trabajo con el usuario

- Responder **en el idioma del usuario** (catalán o castellano), de forma concisa.
- **Verificar antes de cambiar.** Al depurar: reproducir, aislar y confirmar la causa antes de proponer un arreglo.
- No dar por hecho que el usuario ha hecho un paso que no ha confirmado.
- Si hay una alternativa mejor, decirlo brevemente y argumentado.
- Cambio grande (más de un archivo o más de ~50 líneas): explicar el plan y esperar confirmación.
- Si algo no funciona como dice el diseño: **parar, informar con lo verificado y esperar**. No probar alternativas por cuenta propia.
- No hacer commit ni push sin que el usuario lo pida.

## 3. Estilo de código (obligatorio)

Programación estructurada clásica, código didáctico:

- **Un único `return`, como última sentencia.** Las funciones sin valor (incluidos los callbacks de Tkinter) no llevan `return`.
- **Prohibidos `break`, `continue` y `for...else`.** `for` solo para recorrer todos los elementos; si puede haber salida anticipada, `while` con índice y variable booleana de control en la condición.
- **Excepciones:** `raise` está permitido para errores reales y no cuenta como salida extra. Cada función documenta en su docstring qué lanza (`Raises:`). Nunca `return` dentro de `try`, `except` o `finally`: asignar `result` y devolverlo después. Capturar excepciones concretas, nunca `except:` genérico. Nunca usarlas como control de flujo normal.
- **`with` obligatorio** para archivos, SQLite y respuestas HTTP.
- **Comprensiones:** solo con un `for` y como máximo un `if` simple. Prohibidas las anidadas o con `if/else`. Prohibidos `yield` y lambdas encadenadas salvo aprobación del usuario.
- **Recursión** permitida (árbol de carpetas), con caso base explícito.
- Variables inicializadas al principio de la función, con nombres descriptivos. Anotaciones de tipo en las firmas.
- Funciones pequeñas: una tarea, con docstring.
- Código, comentarios y docstrings **en inglés**. README en inglés (`README.md`) y en castellano (`README.es.md`), con el mismo contenido. Rutas con `pathlib`.
- Tkinter: la red va en un `threading.Thread`; la interfaz solo se toca desde el hilo principal con `root.after()`. El núcleo nunca importa `tkinter`.

```python
def find_course_by_id(courses: list[dict], course_id: int) -> dict | None:
    """Return the course with the given id, or None if it is not found."""
    result = None
    found = False
    index = 0
    while index < len(courses) and not found:
        if courses[index]["id"] == course_id:
            result = courses[index]
            found = True
        index = index + 1
    return result


def read_config(path: Path) -> dict:
    """Return the parsed config, or an empty dict if the file is missing.

    Raises: json.JSONDecodeError if the file is corrupt.
    """
    result = {}
    try:
        with path.open(encoding="utf-8") as config_file:
            result = json.load(config_file)
    except FileNotFoundError:
        logger.info("Config file not found, using defaults")
    return result
```

## 4. Seguridad (obligatorio)

- **El usuario nunca pega en el chat su token, la URL del QR ni la URL de inicio de sesión. Claude nunca los pide.** Los scripts los leen en local con `getpass.getpass()`.
- El token se guarda **solo con `keyring`**, también en la PoC (servicio `aulari`, usuario `<host><ruta>|<userid>`, ver D6). Nunca en texto plano, configuración, logs, tracebacks ni `print`. Para comprobarlo, mostrar solo `len(token)`.
- **Filtro de logging obligatorio** que censura `token=`, `wstoken=`, `privatetoken`, `userprivateaccesskey` y las rutas `/tokenpluginfile.php/<clave>/`. Los mensajes de excepción pasan por él. `urllib3` en nivel WARNING. Una prueba unitaria lo verifica.
- Las llamadas a la API envían `wstoken` en el cuerpo POST.
- **Nunca falsear el User-Agent** ni saltarse restricciones del servidor. User-Agent honesto: `Aulari/<versión> (+<URL de GitHub>)`.
- El cliente solo llama a una **lista blanca de funciones de lectura** escrita en el código.
- **Claude no ejecuta nada contra un campus real.** Escribe el script y lo ejecuta el usuario, salvo que este lo pida explícitamente en ese mensaje.
- Las respuestas reales grabadas van **fuera del repositorio**, en `%LOCALAPPDATA%\aulari\recordings\`. Claude no lee ese directorio sin permiso. En `tests/fixtures/` solo van versiones anonimizadas.
- Todo nombre que venga de Moodle pasa por `sanitize_filename()` y toda ruta final se comprueba con `resolve()` para que quede dentro del destino (reglas en `docs/design.md` §5).
- La app **nunca abre ni ejecuta** lo que descarga.
- Trato al servidor: descargas secuenciales, timeouts y espera creciente ante 429/503.

## 5. Entorno y comandos (Windows, PowerShell)

- Python 3.14. Entorno: `py -3.14 -m venv .venv`, `.venv\Scripts\Activate.ps1`, `pip install -r requirements-dev.txt` y `pip install -e .` (paquete editable: `aulari` se importa sin tocar `sys.path`).
- Dependencias declaradas en `requirements.in` y `requirements-dev.in`, y fijadas con hashes en `requirements.txt` y `requirements-dev.txt` (`pip-compile --generate-hashes --strip-extras --allow-unsafe`). Aprobadas: `requests`, `keyring`, `tzdata` (zonas horarias en Windows), `ruff`, `pyinstaller` y `pip-tools` (solo para compilar, aprobados el 2026-11-07), y `setuptools` solo como backend de compilación (fijado en `pyproject.toml`). **Cualquier otra se pide antes de instalarla.**
- Tests: `python -m unittest discover -s tests -v`, antes de cada commit.
- Formato y lint: `ruff format` y `ruff check`. En `pyproject.toml` se desactivan las reglas que contradicen el estilo: `RET501`–`RET505`, `SIM110`, `SIM111`, `PLR1714` y `C4`.
- Comandos de PowerShell: rutas con `\`, `Remove-Item` en lugar de `rm -rf`.
- Git: `main` estable y una rama por fase (`fase-1-poc`). Commits pequeños, en inglés e imperativo. `git status` antes de cada commit.

## 6. Repositorio


```
aulari/
├── CLAUDE.md
├── .gitattributes
├── README.md
├── LICENSE
├── THIRD_PARTY_LICENSES
├── pyproject.toml
├── requirements.txt / requirements-dev.txt
├── .gitignore
├── docs/design.md
├── poc/                    # proof of concept code only, no outputs
├── src/aulari/
│   ├── auth.py             # login flow, token in keyring
│   ├── moodle_client.py    # whitelisted Moodle web service calls
│   ├── sync.py             # orchestrates a sync run
│   ├── organizer.py        # folders, sanitized names, versions
│   ├── registry.py         # SQLite: what was downloaded
│   ├── calendar_export.py  # calendar
│   ├── config.py           # user settings (JSON, no secrets)
│   ├── log_filter.py       # secret redaction
│   ├── strings/es.json     # UI texts
│   └── ui/                 # Tkinter
└── tests/
    ├── fixtures/           # anonymized API responses only
    └── test_*.py
```

El `.gitignore` ya existe: ampliarlo **antes** de crear cualquier archivo nuevo que no deba subirse. El `.spec` de PyInstaller sí se sube.


## 7. Fase 1: prueba de concepto de autenticación

Objetivo: saber cómo se obtiene un token legítimamente en e-aules. Script de consola en `poc/`.

1. Llamar a `tool_mobile_get_public_config` (no requiere sesión) y anotar `typeoflogin`, `qrcodetype`, `launchurl`, versión del sitio y si es HTTPS.
2. Según el resultado, plantear al usuario el método (decisión D1 en `docs/design.md`). **No falsear el User-Agent para el QR.**
3. Con el token obtenido: `core_webservice_get_site_info`, `core_enrol_get_users_courses` y `core_course_get_contents` de un curso. Comprobar si la URL de e-aules cambia por curso académico.
4. Comprobar si el sitio permite la exportación nativa del calendario (`core_calendar_get_calendar_export_token`), para la decisión D3.
5. Grabar las respuestas en `%LOCALAPPDATA%\aulari\recordings\`.
6. Escribir en `docs/design.md` §10 lo verificado y lo descartado.
