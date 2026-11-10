<p align="center"><img src="packaging/aulari-logo-1024.png" alt="Logo de Aulari: una carpeta con una flecha de descarga" width="128"></p>

# Aulari

**Descarga y ordena el material de tu campus. Funciona con Moodle™.**

[Read in English](README.md)

Aulari es una aplicación de escritorio para estudiantes. Descarga los archivos de tus asignaturas de un sitio Moodle, los coloca en carpetas ordenadas, los mantiene al día y exporta tus entregas a un archivo de calendario. Está pensada para el Campus Virtual de la UAB («e-aules»), pero funciona con cualquier Moodle que tenga activado el servicio de la app móvil.

> Aulari es un proyecto independiente. No está afiliado ni respaldado por Moodle Pty Ltd ni por ninguna universidad. Moodle™ es una marca registrada de Moodle Pty Ltd.

## Funciones

- **Inicio de sesión con el sistema de acceso de tu universidad** (por ejemplo, CAS): inicias sesión en tu propio navegador y, en Windows, la aplicación recibe la sesión sola (en macOS, de momento, copias una dirección a mano; ver más abajo). Nunca se escribe ninguna contraseña en la aplicación.
- **Carpetas ordenadas**: `<destino>/<curso académico>/<asignatura>/<NN - sección>/`, con alias cortos de asignatura (`Camps i Ones Electromagnètiques` → `CiOE`) que puedes cambiar.
- **Nunca se sobrescribe nada**: cuando un profesor sube una versión nueva, se guarda al lado de la antigua como `Nombre (AAAA-MM-DD).ext`. Los archivos eliminados del campus se quedan en tu disco.
- **Archivos de recursos, carpetas, páginas y enunciados de tareas**, y los enlaces externos como accesos directos `.url`.
- **Calendario**: tus entregas en un archivo `.ics` que puedes importar en cualquier calendario (probado con Proton Calendar; al volver a importar no se duplican los eventos).
- **Próximas entregas**, **avisos nuevos**, **tus entregas** (estado, nota y comentarios del profesor) y **tus calificaciones** dentro de la aplicación.
- **`Novedades.txt`**: un registro de lo que ha añadido o actualizado cada sincronización.
- **Recuperar archivos borrados**, abrir la carpeta de cada asignatura y varios cursos académicos.

## Requisitos

- Windows 10 u 11, o macOS en un Mac con Apple Silicon (M1 o posterior).
- Un sitio Moodle con el servicio de la app móvil activado (la mayoría de universidades lo tienen, porque la app oficial de Moodle lo necesita).
- Para ejecutarla desde el código fuente: Python 3.14 (en macOS, el instalador de python.org, que incluye Tk).

## Instalación

### Windows

Descarga `Aulari-<versión>-windows.zip` de las [versiones](https://github.com/ALar89/aulari/releases/latest), descomprímelo donde quieras y abre `Aulari.exe` dentro de la carpeta.

El ejecutable todavía no está firmado, así que la primera vez Windows SmartScreen muestra *Windows protegió su PC*. Pulsa **Más información → Ejecutar de todas formas**; no vuelve a preguntar.

Si quieres, comprueba antes la descarga: la versión incluye su SHA-256 (`Get-FileHash Aulari-<versión>-windows.zip`) y una atestación de procedencia (`gh attestation verify Aulari-<versión>-windows.zip --repo ALar89/aulari`).

Para actualizar, descarga el zip nuevo y descomprímelo encima de la carpeta anterior. La configuración y la sesión se conservan, porque se guardan fuera de ella.

### macOS

Descarga `Aulari-<versión>-macos-arm64.dmg` de las [versiones](https://github.com/ALar89/aulari/releases), ábrelo y arrastra **Aulari** a **Aplicaciones**.

La aplicación **no está firmada ni notarizada por Apple** (hace falta una cuenta de desarrollador de Apple, de pago), así que la primera vez macOS la bloquea con un mensaje que dice que Apple no ha podido verificarla. Para abrirla:

1. Abre Aulari una vez y cierra el mensaje.
2. Ve a **Ajustes del Sistema → Privacidad y seguridad**, baja hasta *Seguridad* y pulsa **Abrir igualmente** junto al mensaje sobre Aulari.
3. Confirma con tu contraseña o Touch ID. macOS no volverá a preguntar para esta versión.

Si quieres, comprueba antes la descarga: la versión incluye su SHA-256 (`shasum -a 256 -c Aulari-<versión>-macos-arm64.dmg.sha256`) y una atestación de procedencia (`gh attestation verify Aulari-<versión>-macos-arm64.dmg --repo ALar89/aulari`).

### Desde el código fuente

Windows, en PowerShell:

```powershell
git clone https://github.com/ALar89/aulari.git
cd aulari
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install --require-hashes -r requirements.txt
pip install --no-deps -e .
python -m aulari
```

macOS, en Terminal:

```bash
git clone https://github.com/ALar89/aulari.git
cd aulari
python3.14 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install --no-deps -e .
python -m aulari
```

## Cómo se usa

1. **Inicia sesión**: pulsa *Abrir navegador*, entra en tu campus como siempre y acepta cuando el navegador pregunte si quieres abrir la aplicación. La ventana continúa sola.
   En macOS el inicio de sesión automático aún no está disponible. En Google Chrome u otro navegador basado en Chromium, abre una pestaña nueva con las herramientas de desarrollo grabando (*Red* → *Conservar registro*), pulsa **Copiar dirección de inicio** en Aulari y pégala en esa pestaña. Después de iniciar sesión, copia la cabecera `Location` de la petición `launch.php` (estado 302, empieza por `moodlemobile://`) y pégala en la aplicación. La pantalla de inicio de sesión muestra estos pasos. También funciona si tu sesión del campus sigue abierta en el navegador, que redirige al instante y deja la pestaña en blanco.
2. **Elige tus asignaturas** con las casillas y una **carpeta de destino**.
3. Pulsa **Sincronizar**. El resumen muestra lo nuevo; el calendario se exporta a `<destino>/<curso académico>/calendari.ics`.

La interfaz está en catalán, castellano e inglés (selector en la parte inferior de la ventana; por defecto sigue el idioma de Windows).

## Dónde se guardan tus datos

| Qué | Dónde |
|---|---|
| Token de sesión | Administrador de credenciales de Windows o Llavero de macOS (mediante `keyring`), nunca en un archivo |
| Configuración, registro de descargas, logs | Windows: `%LOCALAPPDATA%\aulari\`. macOS: `~/Library/Application Support/aulari/` |
| Material descargado | La carpeta de destino que elijas |

En Windows, durante el inicio de sesión, la aplicación se registra, **solo para tu usuario de Windows**, como programa para los enlaces `moodlemobile://`, y quita ese registro en cuanto termina.

## Seguridad y privacidad

- Solo lectura: la aplicación solo llama a una lista fija de funciones de lectura del servicio web de Moodle. Nunca entrega, publica ni cambia nada en tu campus.
- El token solo viaja a tu sitio Moodle, dentro de peticiones HTTPS, y se elimina de cualquier mensaje del log.
- Sin telemetría: no se envía nada a ningún sitio salvo a tu Moodle.
- La aplicación nunca abre ni ejecuta los archivos que descarga.
- Identificación honesta: las peticiones llevan el User-Agent `Aulari/<versión>`; la aplicación no se hace pasar por la app oficial de Moodle.
- Límites que conviene conocer: el almacén de credenciales del sistema (Administrador de credenciales de Windows, Llavero de macOS) protege el token de copias en texto plano y de las copias de seguridad, pero no de un programa malicioso que se ejecute con tu propio usuario. En algunos sitios (e-aules incluido) los estudiantes no pueden revocar el token desde su perfil; caduca solo al cabo de un tiempo.

## Solo para uso personal

El material de las asignaturas pertenece a sus autores y es para los estudiantes matriculados. Aulari sirve para tener **tu propia** copia ordenada. No compartas ni publiques el material descargado.

## Limitaciones

- De momento, solo Windows y macOS (Apple Silicon); Linux está previsto.
- En macOS aún no hay inicio de sesión ni sincronización automáticos, la aplicación no está notarizada, los archivos descargados no se marcan como procedentes de internet y los enlaces externos se guardan como accesos directos `.url` de Windows. Después de una actualización, macOS puede volver a preguntar si Aulari puede usar el Llavero.
- No se descargan el texto HTML de las páginas ni los vídeos de plataformas externas.
- Un solo equipo por carpeta de destino (otro equipo puede tomar el relevo).

## Desarrollo

```powershell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
ruff format
ruff check
```

En macOS los mismos comandos funcionan en Terminal (`source .venv/bin/activate` en lugar de `Activate.ps1`). Para compilar allí la aplicación y la imagen de disco:

```bash
pyinstaller --clean --noconfirm aulari.spec
packaging/make_dmg.sh Aulari-prueba.dmg
```

Las decisiones de diseño, lo verificado y los riesgos están en [`docs/design.md`](docs/design.md).

## Licencia

MIT. Consulta [`LICENSE`](LICENSE). El texto oficial de la licencia está en inglés.
