# -*- coding: utf-8 -*-
"""
Sitio de citación y confirmación – Creatón de Practicantes
Talent Attraction & EVP | Bavaria (AB InBev Colombia)

Rutas:
  GET  /           -> Formulario para ingresar la cédula
  POST /consultar  -> Busca la cédula en datos.csv y muestra la citación
  POST /confirmar  -> Guarda la respuesta (SI / NO) en Supabase

Render:
  Build Command: pip install -r requirements.txt
  Start Command: gunicorn bot:app
  Variables de entorno: SUPABASE_URL y SUPABASE_KEY
"""

import os
import re
import unicodedata

import pandas as pd
from flask import Flask, render_template, request
from jinja2 import ChoiceLoader, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_DATOS = os.path.join(BASE_DIR, "datos.csv")

app = Flask(__name__)

# Busca index.html en la carpeta templates/ y, si no está, en la raíz del repo.
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(os.path.join(BASE_DIR, "templates")),
    FileSystemLoader(BASE_DIR),
])

# ---------------------------------------------------------------------------
# Conexión a Supabase (si faltan las variables, la página sigue funcionando
# pero no podrá guardar confirmaciones).
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = None

if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] No se pudo conectar a Supabase: {e}")
else:
    print("[AVISO] SUPABASE_URL / SUPABASE_KEY no configuradas.")

TABLA = "respuestas"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _sin_tildes(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c))


def _normalizar_columna(nombre):
    """'Descripción ' -> 'descripcion'"""
    return re.sub(r"[^a-z]", "", _sin_tildes(nombre).lower())


# Nombres "oficiales" de las columnas que usa la página
COLUMNAS = {
    "nombre": "Nombre",
    "cedula": "Cedula",
    "vacante": "Vacante",
    "fecha": "Fecha",
    "hora": "Hora",
    "descripcion": "Descripcion",
    "ubicacion": "Ubicacion",
}


def limpiar_cedula(valor):
    """Deja solo los dígitos: '1.023.456.789' -> '1023456789'; 1023456789.0 -> '1023456789'"""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    if re.fullmatch(r"\d+\.0+", texto):
        texto = texto.split(".")[0]
    return re.sub(r"\D", "", texto)


def cargar_datos():
    """Lee datos.csv (acepta coma o punto y coma, UTF-8 o Latin-1)."""
    df = None
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(ARCHIVO_DATOS, sep=None, engine="python",
                             dtype=str, encoding=codificacion)
            break
        except UnicodeDecodeError:
            continue
    if df is None:
        raise RuntimeError("No se pudo leer datos.csv")

    # Renombra columnas aunque vengan con tildes, mayúsculas o espacios
    renombrar = {}
    for col in df.columns:
        clave = _normalizar_columna(col)
        if clave in COLUMNAS:
            renombrar[col] = COLUMNAS[clave]
    df = df.rename(columns=renombrar)

    for col in COLUMNAS.values():
        if col not in df.columns:
            df[col] = ""

    return df.fillna("")


def estado_confirmacion(cedula):
    """Devuelve 'SI', 'NO' o None según lo guardado en Supabase."""
    if supabase is None:
        return None
    try:
        res = supabase.table(TABLA).select("respuesta").eq("cedula", cedula).execute()
        if res.data:
            return str(res.data[0].get("respuesta", "")).upper() or None
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Consultando Supabase: {e}")
    return None


def buscar_candidato(cedula_limpia):
    df = cargar_datos()
    df["Cedula"] = df["Cedula"].apply(limpiar_cedula)
    resultado = df[df["Cedula"] == cedula_limpia]
    if resultado.empty:
        return None
    fila = resultado.iloc[0].to_dict()
    fila = {k: str(v).strip() for k, v in fila.items()}
    fila["PrimerNombre"] = fila.get("Nombre", "").split(" ")[0].title() if fila.get("Nombre") else ""
    return fila


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def inicio():
    return render_template("index.html", vista="buscar")


@app.route("/consultar", methods=["POST"])
def consultar():
    cedula_input = request.form.get("cedula", "")
    cedula_limpia = limpiar_cedula(cedula_input)

    if not cedula_limpia:
        return render_template("index.html", vista="buscar",
                               error="Ingresa un número de cédula válido (solo números).")

    try:
        fila = buscar_candidato(cedula_limpia)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] Leyendo datos.csv: {e}")
        return render_template("index.html", vista="buscar",
                               error="Tuvimos un problema consultando la información. Intenta de nuevo en unos minutos.")

    if fila is None:
        return render_template("index.html", vista="buscar", cedula=cedula_input,
                               error="No encontramos esta cédula en la lista de citados. "
                                     "Verifica el número e inténtalo de nuevo.")

    return render_template("index.html", vista="resultado", fila=fila,
                           cedula=cedula_limpia,
                           estado=estado_confirmacion(cedula_limpia))


@app.route("/confirmar", methods=["POST"])
def confirmar():
    cedula = limpiar_cedula(request.form.get("cedula"))
    respuesta = (request.form.get("respuesta") or "").strip().upper()

    if not cedula or respuesta not in ("SI", "NO"):
        return render_template("index.html", vista="buscar",
                               error="La respuesta no es válida. Consulta tu cédula de nuevo.")

    fila = buscar_candidato(cedula)
    if fila is None:
        return render_template("index.html", vista="buscar",
                               error="No encontramos esta cédula en la lista de citados.")

    guardado = False
    if supabase is not None:
        try:
            existente = supabase.table(TABLA).select("*").eq("cedula", cedula).execute()
            if existente.data:   # Ya respondió antes -> actualiza
                supabase.table(TABLA).update({"respuesta": respuesta}).eq("cedula", cedula).execute()
            else:                # Primera respuesta -> inserta
                supabase.table(TABLA).insert({"cedula": cedula, "respuesta": respuesta}).execute()
            guardado = True
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] Guardando en Supabase: {e}")

    return render_template("index.html", vista="confirmado", fila=fila,
                           cedula=cedula, respuesta=respuesta, guardado=guardado)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
