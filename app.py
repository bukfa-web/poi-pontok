import webbrowser
from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify
import sqlite3
import re
import os
import csv
import threading
import time
import sys

app = Flask(__name__)
app.secret_key = "titkoskulcs"

# Adatbázis elérési út beállítása
def get_database_path():
    """ Meghatározza az adatbázis helyét az EXE futtatása esetén """
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS  # PyInstaller által generált ideiglenes mappa
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))  # Normál futtatás esetén

    return os.path.join(base_path, "database.db")

DATABASE = get_database_path()

# Adatbázis kapcsolat
def get_db_connection():
    """ Adatbázis kapcsolat létrehozása """
    conn = sqlite3.connect(DATABASE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# Koordináta formátum ellenőrzés
def is_valid_coordinate(value):
    return bool(re.match(r"^\d{1,2}(\.\d{1,7})?$", value))

@app.route("/")
def index():
    """ Főoldal - listázza az adatokat """
    conn = get_db_connection()
    places = conn.execute("SELECT * FROM places ORDER BY name").fetchall()
    conn.close()
    return render_template("index.html", places=places)

# CRUD, Import, Export, API végpontok
def full_application_code():
    # Az összes funkció bekerül ide
    pass  # Ezt cseréld le a teljes kódra!

print("\n📌 Regisztrált Flask végpontok:")
print(app.url_map)  # 📌 Kiírja az összes elérhető Flask végpontot

if __name__ == "__main__":
    from waitress import serve
    import os
    port = int(os.environ.get("PORT", 5000))  # Railway által adott port
    serve(app, host="0.0.0.0", port=port)
