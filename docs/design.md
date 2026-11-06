# Campus Sync for Moodle: documento de diseño

Versión 2, revisada tras una auditoría crítica (viabilidad de la API, seguridad y legal, casos límite, instrucciones para Claude Code).

## 1. Decisiones pendientes

Claude Code no las toma por su cuenta. Cada una lleva la opción recomendada.

| ID | Decisión | Recomendación |
|---|---|---|
| D1 | Método de inicio de sesión | **ACEPTADA (2026-11-06): flujo de navegador** (`launch.php`, §4). El QR solo funciona si el cliente se hace pasar por la app oficial (comprobación de User-Agent `MoodleMobile`), lo que choca con la normativa TIC de la UAB. Se confirma en la Fase 1. |
| D2 | Varios PCs con la misma carpeta sincronizada | **Un único equipo propietario en la v1** (§6). Dos registros independientes sobre una carpeta compartida generan duplicados y copias en conflicto. |
| D3 | Calendario | **Usar la exportación iCal nativa de Moodle** si el sitio la permite; exportador propio solo como respaldo (§8). Ahorra una fase entera y los UID ya son estables. |
| D4 | Alias de asignatura | Conservar conectores "i"/"y" en minúscula (`CiOE`, `SDiLDH`) o ignorarlos (`COE`). Recomendado: conservarlos en minúscula. |
| D5 | Nombre del proyecto | "Moodle" es marca registrada: usar la fórmula "… for Moodle" y nunca el logotipo de Moodle ni de la UAB. |

## 2. Resumen

- **Objetivo principal:** ahorrar el tiempo de descargar y ordenar a mano el material del campus.
- **Objetivo secundario:** compartirla como proyecto de código abierto y de portfolio.
- **Alcance:** cualquier Moodle con la app móvil activada; UAB por defecto.

## 3. Requisitos funcionales (v1)

| ID | Requisito |
|---|---|
| RF1 | URL del campus configurable, UAB por defecto. Si el sitio no permite el acceso, mensaje claro. |
| RF2 | Inicio de sesión según D1. **Cerrar sesión** borra el token del keyring y explica cómo revocarlo en el perfil de Moodle. |
| RF3 | Selección de asignaturas con casillas; se recuerda la última selección. Por defecto, cursos en curso (`core_course_get_enrolled_courses_by_timeline_classification`, `inprogress`). Los cursos nuevos se proponen, no se descargan sin confirmar. |
| RF4 | Alias de carpeta por asignatura (§5), editable. Cambiarlo después mueve la carpeta y actualiza el registro en una sola transacción. |
| RF5 | Descarga de archivos (`resource`), carpetas (`folder`, conservando su `filepath`) y adjuntos de tareas (`introattachments`; las imágenes incrustadas de `introfiles` se ignoran). Enlaces externos como `.url` seguro (§5). |
| RF6 | Estructura `<destino>/<curso académico>/<alias>/<NN - sección>/`. Sección 0 = `00 - General`. |
| RF7 | Versiones nuevas como `Name (AAAA-MM-DD).ext`, fecha en hora de Europa/Madrid; si coincide, `AAAA-MM-DD HHMM` y después ` (2)`. Nunca se sobrescribe. |
| RF8 | "Volver a descargar" a nivel de archivo, sección o curso. |
| RF9 | Calendario según D3. |
| RF10 | Sincronización manual con progreso y cancelación (corte seguro entre archivos). Al terminar, resumen: nuevos, versiones nuevas, eliminados en Moodle, errores. |
| RF11 | Primera ejecución con material ya descargado a mano: si el archivo de destino existe con el mismo tamaño, se adopta en lugar de duplicarlo. |
| RF12 | Aviso en la primera ejecución: el material es de uso personal y no debe compartirse. |

## 4. Autenticación

Verificado en el código fuente de Moodle 4.5 (ver §10):

- **QR** (`tool_mobile_get_tokens_for_qr_login`): exige User-Agent de la app oficial, HTTPS, clave de 10 minutos y, por defecto, la misma IP. Un cliente de terceros solo funciona falseando el User-Agent. **Descartado salvo que la Fase 1 demuestre lo contrario sin falsear nada.**
- **Flujo de navegador** (`admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=<aleatorio>&urlscheme=<esquema>`): el usuario inicia sesión en su navegador (SSO incluido) y Moodle redirige a `<esquema>://token=<base64>`. No comprueba el User-Agent. Requiere que `typeoflogin` sea navegador, navegador integrado o haya OAuth2. En Windows, o se registra el esquema de URL o el usuario copia la URL final y el script la lee con `getpass`.
- **"Claves de seguridad"** del perfil: **no sirve**, desde Moodle 4.3 no muestra el valor del token.
- El token caduca (por defecto 12 semanas). Ante `invalidtoken`, parar la sincronización limpiamente y pedir iniciar sesión de nuevo.
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

- **Ubicación:** carpeta de datos de la app (`%LOCALAPPDATA%\campus-sync\`), **nunca** dentro del destino sincronizado. Un SQLite en una carpeta de nube se corrompe.
- **Clave de un archivo:** `(site_url, user_id, cmid, filearea, filepath, filename)`. Ni `fileurl` (cambia con `revision`) ni el ID del archivo (cambia al volver a subirlo) son estables, y la API no devuelve `contenthash`.
- **Versión nueva:** cambia `timemodified` **o** `filesize`. Si tras descargar a temporal el SHA-256 coincide con el anterior, no se crea versión: solo se actualiza la fecha.
- **Campos:** ruta **relativa al destino**, `timemodified`, `filesize`, `sha256`, estado (`present`, `missing`, `removed_in_moodle`).
- **Secciones:** tabla `section_id → nombre de carpeta`, fijado al crearla. Si el profesor renombra o reordena secciones, o mueve un archivo de sección, no se mueve nada en el disco.
- **Versión nueva de un archivo que el usuario movió o borró:** siempre se descarga; va junto a la ruta registrada si existe y, si no, a la carpeta de su sección. Se destaca en el resumen.
- **Eliminado u oculto en Moodle:** nunca se borra en local; estado `removed_in_moodle`.
- **Atomicidad:** descargar a `<destino>\.campus-sync-tmp\` (mismo volumen), comprobar tamaño, mover con creación exclusiva y después confirmar en el registro. Al arrancar, un archivo en la ruta esperada sin registro y con el mismo tamaño se adopta.
- **Archivos solo en la nube** (placeholders de Proton u OneDrive): la app solo comprueba si existen; nunca lee su contenido.
- **Bloqueo:** un archivo de bloqueo impide dos instancias a la vez. Un archivo abierto por otro programa es un error de ese archivo y se reintenta en la próxima sincronización.
- **Equipo propietario (D2):** `<destino>\.campus-sync\owner.json` con el ID de la máquina. Otra instancia que lo encuentre se niega a sincronizar y ofrece "tomar el relevo".

## 7. API de Moodle

- **Lista blanca** de funciones de lectura: `core_webservice_get_site_info`, `tool_mobile_get_public_config`, `core_enrol_get_users_courses`, `core_course_get_enrolled_courses_by_timeline_classification`, `core_course_get_contents`, `mod_assign_get_assignments`, `core_calendar_get_calendar_events`, `core_calendar_get_calendar_export_token`.
- **Contenido visible:** `contents` solo llega si `uservisible`. Los `introattachments` pueden no aparecer fuera de plazo si el profesor lo configura así.
- **Descarga:** `fileurl` por `webservice/pluginfile.php`, añadiendo el token.
- **Calendario propio (respaldo de D3):** `core_calendar_get_calendar_events` con **todos los `courseids` seleccionados** y un rango `timestart`/`timeend`. Si no se pasan cursos, no devuelve los eventos de curso.
- Comprobar en la Fase 1 si la URL de e-aules cambia por curso académico; en ese caso, el sitio se guarda por curso.

## 8. Calendario

- **Opción preferida (D3):** exportación iCal nativa de Moodle. El token de exportación es un secreto y se trata como el token principal.
- **Respaldo propio:** `UID = moodle-<eventid>@<host>`, horas en UTC, `DTSTAMP` y `SEQUENCE`, `DTEND = DTSTART` en eventos sin duración, ventana de hoy −30 días a +1 año.
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

Pendiente de completar en la Fase 1: configuración real de e-aules.

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
| Proton Calendar duplica eventos al reimportar | Exportación nativa con suscripción, o borrar e importar |
| Cambios en Moodle rompen la app | Pruebas con respuestas grabadas; código abierto |
| Antivirus o SmartScreen bloquean el ejecutable | `--onedir`, compilación en CI, SignPath |

## 13. Plan por fases

1. **Prueba de concepto de autenticación** (ver `CLAUDE.md` §7).
2. **Núcleo sin interfaz:** cliente, registro, saneado de nombres, descarga atómica, versiones.
3. **Calendario** según D3.
4. **Interfaz Tkinter.**
5. **Empaquetado y publicación.**

## 14. Fuera de alcance v1

Páginas HTML y vídeos, sincronización automática, macOS y Linux, varios equipos sincronizando a la vez, servicio web con credenciales ajenas, clasificación con IA, ejecución en Raspberry Pi.
