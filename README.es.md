<p align="center"><img src="packaging/aulari-logo-1024.png" alt="Logo de Aulari: una carpeta con una flecha de descarga" width="128"></p>

# Aulari

**Descarga y ordena el material de tu campus. Funciona con Moodle™.**

[Read in English](README.md)

Aulari es una aplicación de escritorio para estudiantes. Descarga los archivos de tus asignaturas de un sitio Moodle, los coloca en carpetas ordenadas, los mantiene al día y exporta tus entregas a un archivo de calendario. Está pensada para el Campus Virtual de la UAB («e-aules»), pero funciona con cualquier Moodle que tenga activado el servicio de la app móvil.

> Aulari es un proyecto independiente. No está afiliado ni respaldado por Moodle Pty Ltd ni por ninguna universidad. Moodle™ es una marca registrada de Moodle Pty Ltd.

## Funciones

- **Inicio de sesión automático con el sistema de acceso de tu universidad** (por ejemplo, CAS): inicias sesión en tu propio navegador y la aplicación recibe la sesión sola. Nunca se escribe ninguna contraseña en la aplicación.
- **Carpetas ordenadas**: `<destino>/<curso académico>/<asignatura>/<NN - sección>/`, con alias cortos de asignatura (`Camps i Ones Electromagnètiques` → `CiOE`) que puedes cambiar.
- **Nunca se sobrescribe nada**: cuando un profesor sube una versión nueva, se guarda al lado de la antigua como `Nombre (AAAA-MM-DD).ext`. Los archivos eliminados del campus se quedan en tu disco.
- **Archivos de recursos, carpetas, páginas y enunciados de tareas**, y los enlaces externos como accesos directos `.url`.
- **Calendario**: tus entregas en un archivo `.ics` que puedes importar en cualquier calendario (probado con Proton Calendar; al volver a importar no se duplican los eventos).
- **Próximas entregas**, **avisos nuevos**, **tus entregas** (estado, nota y comentarios del profesor) y **tus calificaciones** dentro de la aplicación.
- **`Novedades.txt`**: un registro de lo que ha añadido o actualizado cada sincronización.
- **Recuperar archivos borrados**, abrir la carpeta de cada asignatura y varios cursos académicos.

## Requisitos

- Windows 10 u 11.
- Un sitio Moodle con el servicio de la app móvil activado (la mayoría de universidades lo tienen, porque la app oficial de Moodle lo necesita).
- Para ejecutarla desde el código fuente: Python 3.14.

## Instalación (desde el código fuente)

```powershell
git clone https://github.com/ALar89/aulari.git
cd aulari
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
python -m aulari
```

Se publicará un ejecutable de Windows en las versiones del repositorio.

## Cómo se usa

1. **Inicia sesión**: pulsa *Abrir navegador*, entra en tu campus como siempre y acepta cuando el navegador pregunte si quieres abrir la aplicación. La ventana continúa sola.
2. **Elige tus asignaturas** con las casillas y una **carpeta de destino**.
3. Pulsa **Sincronizar**. El resumen muestra lo nuevo; el calendario se exporta a `<destino>/<curso académico>/calendari.ics`.

La interfaz está en catalán, castellano e inglés (selector en la parte inferior de la ventana; por defecto sigue el idioma de Windows).

## Dónde se guardan tus datos

| Qué | Dónde |
|---|---|
| Token de sesión | Administrador de credenciales de Windows (mediante `keyring`), nunca en un archivo |
| Configuración, registro de descargas, logs | `%LOCALAPPDATA%\aulari\` |
| Material descargado | La carpeta de destino que elijas |

Durante el inicio de sesión, la aplicación se registra, **solo para tu usuario de Windows**, como programa para los enlaces `moodlemobile://`, y quita ese registro en cuanto termina.

## Seguridad y privacidad

- Solo lectura: la aplicación solo llama a una lista fija de funciones de lectura del servicio web de Moodle. Nunca entrega, publica ni cambia nada en tu campus.
- El token solo viaja a tu sitio Moodle, dentro de peticiones HTTPS, y se elimina de cualquier mensaje del log.
- Sin telemetría: no se envía nada a ningún sitio salvo a tu Moodle.
- La aplicación nunca abre ni ejecuta los archivos que descarga.
- Identificación honesta: las peticiones llevan el User-Agent `Aulari/<versión>`; la aplicación no se hace pasar por la app oficial de Moodle.
- Límites que conviene conocer: el almacén de credenciales de Windows protege el token de copias en texto plano y de las copias de seguridad, pero no de un programa malicioso que se ejecute con tu propio usuario. En algunos sitios (e-aules incluido) los estudiantes no pueden revocar el token desde su perfil; caduca solo al cabo de un tiempo.

## Solo para uso personal

El material de las asignaturas pertenece a sus autores y es para los estudiantes matriculados. Aulari sirve para tener **tu propia** copia ordenada. No compartas ni publiques el material descargado.

## Limitaciones

- De momento solo Windows (macOS y Linux están previstos).
- No se descargan el texto HTML de las páginas ni los vídeos de plataformas externas.
- Un solo equipo por carpeta de destino (otro equipo puede tomar el relevo).

## Desarrollo

```powershell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
ruff format
ruff check
```

Las decisiones de diseño, lo verificado y los riesgos están en [`docs/design.md`](docs/design.md).

## Licencia

MIT. Consulta [`LICENSE`](LICENSE). El texto oficial de la licencia está en inglés.
