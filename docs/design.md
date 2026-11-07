# Aulari: documento de diseño

Versión 2, revisada tras una auditoría crítica (viabilidad de la API, seguridad y legal, casos límite, instrucciones para Claude Code).

## 1. Decisiones pendientes

Claude Code no las toma por su cuenta. Cada una lleva la opción recomendada.

| ID | Decisión | Recomendación |
|---|---|---|
| D1 | Método de inicio de sesión | **ACEPTADA (2026-11-06): flujo de navegador** (`launch.php`, §4). El QR solo funciona si el cliente se hace pasar por la app oficial (comprobación de User-Agent `MoodleMobile`), lo que choca con la normativa TIC de la UAB. Se confirma en la Fase 1. |
| D2 | Varios PCs con la misma carpeta sincronizada | **ACEPTADA (2026-11-06): un único equipo propietario en la v1** (§6). Dos registros independientes sobre una carpeta compartida generan duplicados y copias en conflicto. |
| D3 | Calendario | **ACEPTADA (2026-11-06): exportador propio** con `core_calendar_get_calendar_events` (§8). Más código, pero controlamos el formato y la ventana de fechas, y no hay que guardar un segundo secreto (el token de exportación). |
| D4 | Alias de asignatura | **ACEPTADA (2026-11-06): conservar los conectores "i"/"y" en minúscula** (`CiOE`, `SDiLDH`). |
| D6 | Curso académico de la carpeta (RF6) | **PARCIALMENTE ACEPTADA (2026-11-06):** en e-aules (UAB) se toma de la ruta de `wwwroot` (`2026-27`), porque cada curso es un sitio y `startdate` no es fiable (§10). **Pendiente:** estudiar cómo organizan los cursos otros Moodle antes de fijar la regla general (respaldo con `startdate`, o que el usuario confirme el curso al añadir el sitio). |
| D5 | Nombre del proyecto | "Moodle" es marca registrada. **Revisado el 2026-11-06:** la política de marcas de Moodle (moodle.com/trademarks) dice que no se puede usar "Moodle" en el nombre del software, sin distinguir "… for Moodle"; solo se permite describir la integración ("works with Moodle™"). Recomendado: nombre propio sin "Moodle" y la integración en la descripción; nunca el logotipo de Moodle ni de la UAB. **ACEPTADA (2026-11-06): «Aulari»** (libre en PyPI; en GitHub solo repositorios vacíos con nombres parecidos). Descripción: «Funciona con Moodle™». |

## 2. Resumen

- **Objetivo principal:** ahorrar el tiempo de descargar y ordenar a mano el material del campus.
- **Objetivo secundario:** compartirla como proyecto de código abierto y de portfolio.
- **Alcance:** cualquier Moodle con la app móvil activada; UAB por defecto.

## 3. Requisitos funcionales (v1)

| ID | Requisito |
|---|---|
| RF1 | URL del campus configurable, UAB por defecto. Si el sitio no permite el acceso, mensaje claro. |
| RF2 | Inicio de sesión según D1. **Cerrar sesión** borra el token del keyring y explica que el token sigue siendo válido hasta que caduque: en e-aules no hay forma de revocarlo desde el perfil (ver §10). |
| RF3 | Selección de asignaturas con casillas; se recuerda la última selección. **La primera vez no se marca ninguna** (decidido el 2026-11-06): en e-aules la clasificación `inprogress` incluye también los espacios que no son asignaturas, y el usuario puede no querer todas. Los cursos nuevos se proponen, no se descargan sin confirmar. |
| RF4 | Alias de carpeta por asignatura (§5), editable. Cambiarlo después mueve la carpeta y actualiza el registro en una sola transacción. |
| RF5 | Descarga de archivos (`resource`), carpetas (`folder`, conservando su `filepath`), archivos incrustados en páginas (`page`, sin su `index.html`; decidido el 2026-11-06) y adjuntos de tareas (`introattachments`; las imágenes incrustadas de `introfiles` se ignoran). Enlaces externos como `.url` seguro (§5). |
| RF6 | Estructura `<destino>/<curso académico>/<alias>/<NN - sección>/`. Sección 0 = `00 - General`. |
| RF7 | Versiones nuevas como `Name (AAAA-MM-DD).ext`, fecha en hora de Europa/Madrid; si coincide, `AAAA-MM-DD HHMM` y después ` (2)`. Nunca se sobrescribe. |
| RF8 | "Volver a descargar": en la v1, botón «Recuperar borrados» que vuelve a descargar, en su sitio, los archivos de las asignaturas marcadas que el usuario borró (decidido el 2026-11-06; por archivo o sección, más adelante). |
| RF9 | Calendario según D3. |
| RF10 | Sincronización manual con progreso y cancelación (corte seguro entre archivos). Al terminar, resumen: nuevos, versiones nuevas, eliminados en Moodle, errores. |
| RF11 | Primera ejecución con material ya descargado a mano: si el archivo de destino existe con el mismo tamaño, se adopta en lugar de duplicarlo. |
| RF12 | Aviso en la primera ejecución: el material es de uso personal y no debe compartirse. |

**Ampliación acordada el 2026-11-06** (antes de publicar; en este orden):

| ID | Requisito |
|---|---|
| RF13 | Botón «Abrir carpeta» del destino en el Explorador (nunca abre archivos descargados). |
| RF14 | Archivo `Novedades.txt` en `<destino>/<curso académico>/` con lo nuevo y actualizado de cada sincronización (sustituye a la lista en la ventana, que no cabía). |
| RF15 | Próximas entregas y fechas en la ventana principal, a partir de los eventos del calendario. |
| RF16 | Aviso de anuncios nuevos del foro de avisos (`news`) de cada asignatura: título y asignatura, sin guardar los foros. |
| RF17 | Estado de las entregas propias de cada tarea (entregada o no, nota y comentarios del profesor). |
| RF18 | Botón «Calificaciones» que abre una tabla con todas las asignaturas y su nota. |
| RF19 | Varios cursos académicos (sitios) a la vez, según D6: selector «Curso» en la ventana, añadir y quitar cursos; cada sitio guarda su usuario, token y selección. Se sincroniza solo el curso activo. |

RF16–RF18 usan funciones de lectura nuevas (`mod_forum_*`, `mod_assign_get_submission_status`, `gradereport_*`): se comprueban antes en e-aules con un script de prueba. Descartados por ahora: guardar los foros completos. Más adelante: sincronización automática e idiomas catalán e inglés.

## 4. Autenticación

Verificado en el código fuente de Moodle 4.5 (ver §10):

- **QR** (`tool_mobile_get_tokens_for_qr_login`): exige User-Agent de la app oficial, HTTPS, clave de 10 minutos y, por defecto, la misma IP. Un cliente de terceros solo funciona falseando el User-Agent. **Descartado salvo que la Fase 1 demuestre lo contrario sin falsear nada.**
- **Flujo de navegador** (`admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=<aleatorio>&urlscheme=<esquema>`): el usuario inicia sesión en su navegador (SSO incluido) y Moodle redirige a `<esquema>://token=<base64>`. No comprueba el User-Agent. Requiere que `typeoflogin` sea navegador, navegador integrado o haya OAuth2. En Windows, o se registra el esquema de URL o el usuario copia la URL final y el script la lee con `getpass`.
- **"Claves de seguridad"** del perfil: **no sirve**, desde Moodle 4.3 no muestra el valor del token.
- El token caduca (por defecto 12 semanas; no verificado en e-aules). En e-aules, Preferencias no tiene «Claves de seguridad»: el estudiante no puede revocar el token por su cuenta. Ante `invalidtoken`, parar la sincronización limpiamente y pedir iniciar sesión de nuevo.
- Se descarta y nunca se guarda `privatetoken`.

## 5. Nombres y rutas

**Alias de asignatura:** iniciales de las palabras significativas. Ignorar artículos y preposiciones ("de", "del", "la", "el", "l'", "d'", con apóstrofo recto o tipográfico), quitar acentos, mantener números y romanos (`Càlcul II` → `C2`), conectores según D4. El alias debe ser único dentro del curso académico; si choca, se pide al usuario.

**Curso académico:** a partir de `startdate`, con corte el 1 de septiembre (`2026-27`). Se fija al crear la carpeta y no se recalcula. `startdate = 0` → carpeta `Sin curso`.

**`sanitize_filename()`**, para todo nombre que venga de Moodle (archivo, carpeta, sección, `filepath`):
- Sustituir `<>:"/\|?*` y caracteres de control; rechazar `..`.
- Quitar puntos y espacios finales.
- Evitar nombres reservados (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`), también con extensión.
- Normalizar a NFC.
- Limitar sección a 60 caracteres, archivo a 100 y ruta total a 240, truncando con un hash corto.
- Colisiones (sin distinguir mayúsculas): ` (2)`, ` (3)`… deterministas; el nombre final se guarda en el registro.
- Comprobar con `Path.resolve()` que la ruta final queda dentro del destino.

**Archivos `.url`:** solo esquemas `http`/`https`; rechazar URLs con `\r` o `\n`; contenido exacto `[InternetShortcut]` + `URL=`. Si la URL cambia en Moodle, se crea un `.url` nuevo como versión.

**Mark-of-the-Web:** las descargas llevan `Zone.Identifier` para que Office las abra en vista protegida.

## 6. Registro local (SQLite)

- **Ubicación:** carpeta de datos de la app (`%LOCALAPPDATA%\aulari\`), **nunca** dentro del destino sincronizado. Un SQLite en una carpeta de nube se corrompe.
- **Clave de un archivo:** `(site_url, user_id, cmid, filearea, filepath, filename)`. Ni `fileurl` (cambia con `revision`) ni el ID del archivo (cambia al volver a subirlo) son estables, y la API no devuelve `contenthash`.
- **Versión nueva:** cambia `timemodified` **o** `filesize`. Si tras descargar a temporal el SHA-256 coincide con el anterior, no se crea versión: solo se actualiza la fecha.
- **Campos:** ruta **relativa al destino**, `timemodified`, `filesize`, `sha256`, estado (`present`, `missing`, `removed_in_moodle`).
- **Secciones:** tabla `section_id → nombre de carpeta`, fijado al crearla. Si el profesor renombra o reordena secciones, o mueve un archivo de sección, no se mueve nada en el disco.
- **Versión nueva de un archivo que el usuario movió o borró:** siempre se descarga; va junto a la ruta registrada si existe y, si no, a la carpeta de su sección. Se destaca en el resumen.
- **Eliminado u oculto en Moodle:** nunca se borra en local; estado `removed_in_moodle`.
- **Atomicidad:** descargar a `<destino>\.aulari-tmp\` (mismo volumen), comprobar tamaño, mover con creación exclusiva y después confirmar en el registro. Al arrancar, un archivo en la ruta esperada sin registro y con el mismo tamaño se adopta.
- **Archivos solo en la nube** (placeholders de Proton u OneDrive): la app solo comprueba si existen; nunca lee su contenido.
- **Bloqueo:** un archivo de bloqueo impide dos instancias a la vez. Un archivo abierto por otro programa es un error de ese archivo y se reintenta en la próxima sincronización.
- **Equipo propietario (D2):** `<destino>\.aulari\owner.json` con el ID de la máquina. Otra instancia que lo encuentre se niega a sincronizar y ofrece "tomar el relevo".

## 7. API de Moodle

- **Lista blanca** de funciones de lectura: `core_webservice_get_site_info`, `tool_mobile_get_public_config`, `core_enrol_get_users_courses`, `core_course_get_contents`, `mod_assign_get_assignments`, `core_calendar_get_calendar_events`.
- **Contenido visible:** `contents` solo llega si `uservisible`. Los `introattachments` pueden no aparecer fuera de plazo si el profesor lo configura así.
- **Descarga:** `fileurl` por `webservice/pluginfile.php`, añadiendo el token.
- **Calendario (D3):** `core_calendar_get_calendar_events` con **todos los `courseids` seleccionados** y un rango `timestart`/`timeend`. Si no se pasan cursos, no devuelve los eventos de curso.
- Comprobar en la Fase 1 si la URL de e-aules cambia por curso académico; en ese caso, el sitio se guarda por curso.

## 8. Calendario

- **Exportador propio (D3, aceptada):** `core_calendar_get_calendar_events` (§7). `UID = moodle-<eventid>@<curso académico>.<host>` (el curso evita choques de id entre los sitios de cada año, D6), horas en UTC, `DTSTAMP` y `SEQUENCE`, ventana de hoy −30 días a +1 año.
- **Eventos sin duración** (decidido el 2026-11-06): **sin `DTEND`**. RFC 5545 exige que `DTEND` sea posterior a `DTSTART`; un `VEVENT` con `DTSTART` de fecha y hora y sin `DTEND` dura cero, que es lo que se quería con `DTEND = DTSTART`.
- **`SEQUENCE`:** tabla `calendar_events` del registro; sube cuando cambia el `timemodified` del evento.
- **Archivo** (decidido el 2026-11-06): `<destino>/<curso académico>/calendari.ics`, uno por curso (cada curso es un sitio). Lo genera la app, así que se reemplaza en cada exportación, de forma atómica.
- **Limitación documentada:** importar no es sincronizar. Según el calendario, reimportar puede duplicar eventos, y los eventos borrados en Moodle no desaparecen. Hay que comprobar el comportamiento de Proton Calendar.

## 9. Requisitos no funcionales

- **Plataformas:** Windows en la v1; después macOS (`.webloc` en lugar de `.url`) y Linux.
- **Idiomas:** interfaz en castellano; catalán e inglés después, con archivos de cadenas.
- **Sin IA en el núcleo.**
- **Robustez:** un fallo en un archivo o curso no detiene la sincronización.
- **Logs:** pueden contener IDs y nombres de archivo; nunca el token, el nombre o el correo del usuario, ni respuestas completas. Rotación en la carpeta de datos de la app.
- **Privacidad:** sin telemetría; los datos solo se guardan en el equipo. Si se añade la búsqueda de actualizaciones, avisar de que contacta con GitHub.
- **Seguridad de `keyring`:** protege contra el texto plano y las copias de seguridad, no contra malware del mismo usuario. Documentarlo en el README.

## 10. Hallazgos verificados

Código fuente de Moodle, rama `MOODLE_405_STABLE`:
- QR y User-Agent: `admin/tool/mobile/classes/external.php`, `admin/tool/mobile/classes/api.php`, `lib/classes/useragent.php`.
- Flujo de navegador: `admin/tool/mobile/launch.php`.
- Caducidad del token: `lib/external/classes/util.php`, `admin/settings/security.php`.
- Calendario: `calendar/externallib.php`, `calendar/classes/external/export/token.php`.
- `fileurl` con `revision`: `mod/resource/lib.php`, `mod/folder/lib.php`.

### Fase 1: e-aules (verificado el 2026-11-06)

`tool_mobile_get_public_config` (`poc/public_config.py`, sin sesión, vía `lib/ajax/service-nologin.php`):
- `wwwroot` = `https://e-aules.uab.cat/2026-27`, HTTPS. **El curso académico forma parte de la URL**: cada curso es un sitio distinto (riesgo de §12 confirmado en la práctica; falta comprobar si el sitio del curso anterior sigue accesible).
- `typeoflogin` = 2 (navegador del sistema), `launchurl` = `.../admin/tool/mobile/launch.php`, proveedor de identidad: CAS.
- `release`, `version` y `qrcodetype` no aparecen en la configuración pública.

Flujo de navegador (`poc/browser_login.py`):
- **Funciona** con `service=moodle_mobile_app` y `urlscheme=moodlemobile`, sin tocar el User-Agent. El usuario copia la cabecera `Location` (`moodlemobile://token=...`) desde las DevTools y el script la lee con `getpass`.
- La firma `md5(wwwroot + passport)` se verifica correctamente. Token de 32 caracteres.
- `keyring` en Windows usa `WinVaultKeyring` (Administrador de credenciales).
- `core_webservice_get_site_info`: Moodle **4.5.10+** (Build 20260320), versión `2024100710.05`; coincide con la rama analizada.
- Usuario del keyring: `<host><ruta>|<userid>` (p. ej. `e-aules.uab.cat/2026-27|<id>`) en lugar de `<host>|<userid>`, porque cada curso académico es un sitio distinto.

Cursos y contenidos (`poc/explore_api.py`, 2026-11-06):
- `core_enrol_get_users_courses`: 10 cursos. 5 asignaturas del curso y 5 espacios que no son asignaturas (coordinación, información), con `enddate = 0`.
- **`startdate` no sirve para deducir el curso académico**: en el sitio 2026-27 hay asignaturas con `startdate` 2026-07-20, 2026-08-31 e incluso 2023-07-11. Con la regla de §5 (corte el 1 de septiembre) irían a carpetas de cursos anteriores. Ver decisión D6.
- `shortname` y `fullname` son iguales y llevan ruido: código `[MO86824]`, sufijos `(2026-27)` o `- Grau ... 26-27`. El generador de alias debe quitarlo.
- `core_course_get_contents` (una asignatura): 6 secciones, 49 módulos (`resource`, `folder`, `assign`, `forum`, `label`, `page`, `groupselect`), 12 con `uservisible = false`, 27 archivos (45 MB). **Todos los `fileurl` usan `webservice/pluginfile.php`**, como espera §7. En esta asignatura no hay módulos `url`.

Calendario (D3):
- `core_calendar_get_calendar_export_token` funciona: token de 40 caracteres. No se usará: D3 se decidió por el exportador propio.

Sitio por curso académico:
- `https://e-aules.uab.cat/2025-26` sigue activo con la misma configuración (`typeoflogin` 2, CAS). Cada curso es un sitio distinto, con su propio token. No se ha comprobado qué cursos tiene el usuario en el sitio anterior.

Calendario y tareas (`poc/explore_assign_calendar.py`, 2026-11-06):
- `core_calendar_get_calendar_events` con los 10 `courseids` y la ventana de §8: 6 eventos (5 `due` de `assign`, 1 `close` de `quiz`), sin avisos. **Todos con `timeduration = 0`**: la regla `DTEND = DTSTART` de §8 se aplica a todos.
- `mod_assign_get_assignments` (una asignatura): 5 tareas, 4 con `introattachments`, 8 archivos (8,6 MB), todos por `webservice/pluginfile.php`. No hay `introfiles`.
- `core_course_get_contents` muestra 9 módulos `assign` en esa asignatura, pero `mod_assign_get_assignments` devuelve 5 tareas y 4 avisos. Los 4 avisos son `item: module`, `warningcode: 1`, "No access rights in module context": son las 4 tareas a las que el usuario no tiene acceso (probablemente las ocultas o restringidas, `uservisible = false`; no se ha cruzado con la grabación de contenidos). **Consecuencia:** un aviso de este tipo es normal, no un error; la sincronización lo registra y sigue (RNF de robustez, §9).

### Fase 2: descarga de archivos (verificado el 2026-11-06)

- `webservice/pluginfile.php` **acepta el token en el cuerpo POST** (`token=`), sin ponerlo en la URL: `poc/download_one.py` descargó un archivo de 640 bytes con el mismo tamaño que declara Moodle.
- Los módulos `page` listan un `index.html` generado con `filesize = 0`: no son comparables por tamaño y quedan fuera de la v1 (§14). La sincronización debe ignorarlos.
- **Sincronización real de una asignatura** (`poc/sync_one_course.py`, núcleo completo): primera ejecución, 33 archivos nuevos y 0 errores; segunda ejecución, 33 sin cambios y 0 descargas. Cuadrado con las grabaciones: 25 archivos de `resource`/`folder` + 8 adjuntos de tareas = 33, sin claves duplicadas. Los 2 que faltan para 35 son del único módulo `page`: su `index.html` (`filesize = 0`) y **un PDF de 0,9 MB incrustado en la página**, que hoy se ignora porque los `page` quedan fuera de la v1. **Decidido (2026-11-06):** se descargan los archivos incrustados en páginas, en una subcarpeta con el nombre de la página, sin el `index.html`. Verificado: la tercera ejecución descargó 1 archivo nuevo (el PDF) y dejó 33 sin cambios; total 34 = 35 menos el `index.html`.

### Fase 3: calendario (verificado el 2026-11-06)

- `poc/export_calendar.py` con los 10 cursos: 6 eventos (`SO` 3, `SDiLDH` 1, `EC` 2), ninguno con `DTEND`.
- **Proton Calendar** importa el archivo en un calendario de prueba: hora local correcta (23:59), título `[SO] …` con acentos correctos y duración cero mostrada como «23:59 – 23:59».
- **Reimportar el mismo archivo no duplica eventos** (UID estables): el riesgo de §12 queda resuelto para Proton. Falta ver qué pasa cuando un evento cambia (`SEQUENCE` 1) o desaparece de Moodle.

### Fase 4: captura del token en Windows (verificado el 2026-11-06)

- Sin las DevTools, el navegador no muestra la redirección `moodlemobile://token=…`: Windows no tiene programa para ese esquema y el navegador no hace nada. Pedir al usuario que la copie no es viable sin F12.
- `launch.php` con `urlscheme=campussync` redirige igualmente a **`moodlemobile://`**: e-aules **fuerza el esquema** (`tool_mobile_forcedurlscheme`). Un esquema propio no sirve.
- **Decidido e implementado (2026-11-06):** al pulsar «Abrir navegador» la app registra `moodlemobile://` en `HKCU\Software\Classes` (sin administrador), guarda y luego restaura cualquier manejador anterior, y abre un socket local (`127.0.0.1`, puerto aleatorio, clave de un solo uso en `login_handoff.json`, nunca el token). Windows lanza `--login-callback <url>`, que pasa la URL a la ventana y termina. Al acabar, fallar, agotar 10 minutos o cerrar, se quita el registro. Queda una opción manual (F12) como respaldo.
- **Barra final añadida:** al pasar la URL al programa, llega `moodlemobile://token=<base64>/` (longitud del base64 no múltiplo de 4, «Excess data after padding»). Como Moodle siempre rellena el base64 a múltiplos de 4, se quita una sola `/` final cuando rompe esa longitud. Diagnosticado con `auth.redirect_shape()`, que nunca muestra el token.
- **Verificado de punta a punta:** con la barra corregida, «Abrir navegador» → login CAS → «Open Python» deja la sesión iniciada sin copiar nada; al terminar, `moodlemobile://` vuelve a no tener manejador y no queda `login_handoff.json`. Cerrar y reabrir la app mantiene la sesión. En desarrollo el navegador muestra «Open Python»; con el ejecutable (fase 5) mostrará el nombre de la app.
- **Riesgo aceptado:** el token viaja en la línea de órdenes del proceso de callback, visible para programas del mismo usuario; esos programas ya podrían leer el keyring (§9).

### Fase 4: selección de cursos y primera sincronización desde la interfaz (2026-11-06)

- `core_course_get_enrolled_courses_by_timeline_classification` (`inprogress`) devolvió los **10** cursos como en curso, incluidos los 4 espacios sin fecha de fin (información, coordinación). No sirve para preseleccionar: se elimina su uso y la primera lista sale sin marcar (RF3).
- Sincronización de una asignatura desde la ventana: 34 nuevos, 0 errores, calendario con 3 eventos en `<destino>/2026-27/calendari.ics`. La carpeta de una sección sin archivos no se crea.

### Fase 4: funciones extra (verificado el 2026-11-06)

- Aviso de uso personal en la primera ejecución (RF12): se muestra una vez.
- Cambio de alias (RF4): `SO` → `SO-Renombrada` mueve la carpeta y la siguiente sincronización la encuentra.
- «Recuperar borrados» (RF8): con un archivo borrado a mano, resumen con 1 recuperado y 33 sin cambios.

### Ampliación: avisos, entregas y notas (verificado el 2026-11-06, `poc/explore_extras.py`)

Todas las funciones responden a un estudiante en e-aules:
- `mod_forum_get_forums_by_courses`: 15 foros visibles, 9 de tipo `news` (uno por asignatura). `mod_forum_get_forum_discussions`: devuelve `id`, `name`/`subject`, `message` (HTML), `timemodified`, `created`, `pinned`, `numunread`, `userfullname`… (RF16).
- `mod_assign_get_submission_status` (por `assignid`, no `cmid`): `lastattempt.submission.status` (`submitted`…), `feedback.gradefordisplay` cuando hay nota; sin `feedback` si aún no está corregida (RF17).
- `gradereport_overview_get_course_grades`: 8 cursos con `courseid`, `grade`, `rawgrade`; 3 con nota (RF18).
- `gradereport_user_get_grade_items` (una asignatura): 23 ítems con `itemname`, `gradeformatted`, `rangeformatted`, `percentageformatted`, `feedback`, `gradeishidden`… (RF18).
- Las respuestas contienen datos personales (notas, nombres): solo se guardan en local y nunca se registran en el log.
- **RF19 verificado (2026-11-07):** añadir `e-aules.uab.cat/2025-26`, iniciar sesión y volver a 2026-27 con el selector conserva la sesión y la selección de cada curso.

### Fase 4: cerrar sesión (verificado el 2026-11-06)

- En e-aules, Preferencias solo muestra perfil, idioma, foro, editor, calendario, banco de contenidos, mensajes, notificaciones e insignias: **no hay «Claves de seguridad»**. El texto de «Cerrar sesión» dice que el token sigue válido hasta que caduca y que, si se ha filtrado, hay que avisar al soporte del campus.

### Fase 5: ejecutable (verificado el 2026-11-07)

- `pyinstaller --clean --noconfirm aulari.spec` genera `dist/Aulari/Aulari.exe` (`--onedir`, sin consola, 31 MB) con los textos, `tzdata`, Tk y los backends de `keyring`.
- Probado por el usuario: la ventana abre sin consola, la sesión guardada en el keyring sigue válida, el inicio de sesión muestra «Open Aulari?» gracias al recurso de versión (`packaging/version_info.txt`) y la sincronización funciona.
- Dependencias fijadas con hashes (`requirements*.txt` generados con pip-tools); un entorno limpio instala con `--require-hashes`.

## 11. Legal y distribución

- **Normativa TIC de la UAB** (https://uab.cat/doc/TR_normativa_TIC): cuentas personales e intransferibles (art. 46.4), no dejar el acceso desprotegido (art. 47.1.b), no saltarse restricciones (art. 47.1.d), material solo para matriculados (art. 38). Consecuencias: nunca modo multiusuario ni credenciales ajenas, el token no se exporta y nunca se falsea el User-Agent.
- **Derechos de autor:** precedente de CEDRO contra la UAB (2013) por material del Campus Virtual. El README y la primera ejecución avisan del uso personal; las capturas y los issues nunca muestran material ni nombres reales.
- **Licencia:** MIT, con `THIRD_PARTY_LICENSES` en el ejecutable. Dependencias fijadas con hashes (`pip-compile --generate-hashes`) y Dependabot.
- **Ejecutable:** compilado en GitHub Actions (no en un PC personal), PyInstaller `--onedir`, SHA-256 y atestación de procedencia publicados. Firma gratuita: solicitar SignPath Foundation (la firma de Azure no acepta particulares de España). Comprobar `keyring.get_keyring()` dentro del ejecutable.
- **Donaciones:** consultar a un gestor antes de activarlas; no ofrecer nada a cambio.
- **Nombre:** ver D5.

## 12. Riesgos

| Riesgo | Plan |
|---|---|
| El flujo de navegador no está habilitado en e-aules | Consultar `typeoflogin` en la Fase 1; si no hay vía legítima, el proyecto se replantea |
| La URL de e-aules cambia cada curso | Guardar el sitio por curso académico; nuevo inicio de sesión al cambiar de curso |
| Proton Calendar duplica eventos al reimportar | **Comprobado (2026-11-06): no duplica** gracias a los UID estables. Pendiente: eventos modificados o borrados en Moodle |
| Cambios en Moodle rompen la app | Pruebas con respuestas grabadas; código abierto |
| Antivirus o SmartScreen bloquean el ejecutable | `--onedir`, compilación en CI, SignPath |

## 13. Plan por fases

1. **Prueba de concepto de autenticación** (ver `CLAUDE.md` §7).
2. **Núcleo sin interfaz:** cliente, registro, saneado de nombres, descarga atómica, versiones.
3. **Calendario** según D3.
4. **Interfaz Tkinter.**
5. **Empaquetado y publicación.**

## 14. Fuera de alcance v1

El HTML de las páginas (sí sus archivos incrustados) y vídeos, sincronización automática, macOS y Linux, varios equipos sincronizando a la vez, servicio web con credenciales ajenas, clasificación con IA, ejecución en Raspberry Pi.
