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

@app.route("/api/places", methods=["GET"])
def api_places():
    """ API végpont, ami JSON formátumban visszaadja a helyeket """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, east, north, address FROM places")
    rows = cursor.fetchall()
    conn.close()
    
    places = []
    for row in rows:
        places.append({
            "name": row["name"],
            "east": row["east"],
            "north": row["north"],
            "address": row["address"]
        })
    
    return jsonify(places)

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    """ Hely törlése """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM places WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash("🗑️ Hely sikeresen törölve!", "success")
    return redirect(url_for("index"))

print("\n📌 Regisztrált Flask végpontok:")
print(app.url_map)  # 📌 Kiírja az összes elérhető Flask végpontot

if __name__ == "__main__":
    from waitress import serve
    import os
    port = int(os.environ.get("PORT", 5000))  # Railway által adott port
    serve(app, host="0.0.0.0", port=port)
