import webbrowser
from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify
import sqlite3
import re
import os
import csv
import sys

app = Flask(__name__)
app.secret_key = "titkoskulcs"

# Adatbázis elérési út beállítása
def get_database_path():
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS  # PyInstaller által generált ideiglenes mappa
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))  # Normál futtatás esetén
    return os.path.join(base_path, "database.db")

DATABASE = get_database_path()

def get_db_connection():
    conn = sqlite3.connect(DATABASE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def is_valid_coordinate(value):
    return bool(re.match(r"^\d{1,2}(\.\d{1,7})?$", value))

@app.route("/")
def index():
    conn = get_db_connection()
    places = conn.execute("SELECT * FROM places ORDER BY name").fetchall()
    conn.close()
    return render_template("index.html", places=places)

@app.route("/api/places", methods=["GET"])
def api_places():
    conn = get_db_connection()
    places = conn.execute("SELECT * FROM places ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(row) for row in places])

@app.route("/add", methods=["GET", "POST"])
def add_place():
    if request.method == "POST":
        name = request.form["name"].strip()
        east = "{:.7f}".format(float(request.form["east"].strip()))
        north = "{:.7f}".format(float(request.form["north"].strip()))
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (?, ?, ?, ?, ?)",
                       (name, east, north, address, notes))
        conn.commit()
        conn.close()
        flash("✅ Hely sikeresen hozzáadva!", "success")
        return redirect(url_for("index"))
    
    return render_template("add.html")

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM places WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash("🗑️ Hely sikeresen törölve!", "success")
    return redirect(url_for("index"))

@app.route("/export")
def export_csv():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, east, north, address, notes FROM places")
    places = cursor.fetchall()
    conn.close()
    
    csv_data = "Név,Kelet,Észak,Cím,Megjegyzések\n"
    for place in places:
        csv_data += f"{place['name']},{place['east']},{place['north']},{place['address']},{place['notes']}\n"
    
    response = Response(csv_data.encode("utf-8-sig"), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=helyek_export.csv"
    return response

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 8080))  # Railway által adott port
    serve(app, host="0.0.0.0", port=port)
