import webbrowser
from flask import Flask, render_template, request, redirect, url_for, flash, Response
import sqlite3
import re
import os
import csv
import threading
import time

app = Flask(__name__)
app.secret_key = "titkoskulcs"

import os
import sys

def get_database_path():
    """ Meghatározza az adatbázis helyét az EXE futtatása esetén """
    if getattr(sys, 'frozen', False):  # Ha EXE-ként futtatjuk
        base_path = sys._MEIPASS  # PyInstaller által generált ideiglenes mappa
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))  # Normál futtatás esetén

    return os.path.join(base_path, "database.db")

DATABASE = get_database_path()


def get_db_connection():
    """ Adatbázis kapcsolat létrehozása """
    conn = sqlite3.connect(DATABASE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def is_valid_coordinate(value):
    """ Koordináta formátum ellenőrzése """
    return bool(re.match(r"^\d{1,2}(\.\d{1,7})?$", value))

@app.route("/")
def index():
    """ Főoldal - listázza az adatokat """
    conn = get_db_connection()
    places = conn.execute("SELECT * FROM places ORDER BY name").fetchall()
    conn.close()
    return render_template("index.html", places=places)

@app.route("/add", methods=["GET", "POST"])
def add_place():
    """ Új hely hozzáadása """
    if request.method == "POST":
        name = request.form["name"].strip()
        east = "{:.7f}".format(float(request.form["east"].strip()))
        north = "{:.7f}".format(float(request.form["north"].strip()))
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        conn = get_db_connection()
        cursor = conn.cursor()

        # 🚨 Egyedi értékek ellenőrzése minden oszlopra külön
        error_messages = []

        cursor.execute("SELECT id FROM places WHERE name = ?", (name,))
        if cursor.fetchone():
            error_messages.append("⚠️ A név már létezik az adatbázisban!")

        cursor.execute("SELECT id FROM places WHERE east = ?", (east,))
        if cursor.fetchone():
            error_messages.append("⚠️ A keleti koordináta már létezik az adatbázisban!")

        cursor.execute("SELECT id FROM places WHERE north = ?", (north,))
        if cursor.fetchone():
            error_messages.append("⚠️ Az északi koordináta már létezik az adatbázisban!")

        # Ha van duplikált adat, akkor visszaadunk egy figyelmeztetést és nem mentjük el
        if error_messages:
            for error in error_messages:
                flash(error, "warning")
            conn.close()
            return redirect(url_for("add_place"))

        try:
            cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (?, ?, ?, ?, ?)", 
                           (name, east, north, address, notes))
            conn.commit()
            flash("✅ Hely sikeresen hozzáadva!", "success")
        except sqlite3.IntegrityError:
            flash("⚠️ Az adatok nem kerültek mentésre, mert már léteznek!", "warning")
        finally:
            conn.close()

        return redirect(url_for("index"))

    return render_template("add.html")


@app.route("/import", methods=["GET", "POST"])
def import_csv():
    """ CSV importálás """
    if request.method == "POST":
        file = request.files["file"]
        if not file:
            flash("❌ Nincs fájl kiválasztva!", "danger")
            return redirect(url_for("import_csv"))

        UPLOAD_FOLDER = "uploads"
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)

        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)

        conn = get_db_connection()
        cursor = conn.cursor()

        imported_count = 0
        duplicate_entries = []

        with open(file_path, newline="", encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            if reader.fieldnames:
                reader.fieldnames = [field.strip() for field in reader.fieldnames]

            for row in reader:
                name = row.get("Név", "").strip()
                east = row.get("Kelet", "").strip()
                north = row.get("Észak", "").strip()
                address = row.get("Cím", "").strip()
                notes = row.get("Megjegyzések", "").strip()

                # **Hibás koordináták ellenőrzése**
                if not east.replace('.', '', 1).isdigit() or not north.replace('.', '', 1).isdigit():
                    continue

                # **Duplikáció ellenőrzése (Név is számít!)**
                cursor.execute("SELECT id FROM places WHERE name = ?", (name,))
                if cursor.fetchone():
                    duplicate_entries.append(name)
                    continue

                try:
                    cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (?, ?, ?, ?, ?)", 
                                   (name, east, north, address, notes))
                    imported_count += 1
                except sqlite3.IntegrityError:
                    duplicate_entries.append(name)
                    continue

        conn.commit()
        conn.close()
        os.remove(file_path)

        if imported_count > 0:
            flash(f"✅ {imported_count} új hely importálva!", "success")

        if duplicate_entries:
            flash(f"⚠️ {len(duplicate_entries)} bejegyzés már létezett!", "warning")

        return redirect(url_for("index"))

    return render_template("import.html")

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

@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit(id):
    """ Hely szerkesztése """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 📌 Lekérdezzük a szerkesztett helyet
    cursor.execute("SELECT * FROM places WHERE id = ?", (id,))
    place = cursor.fetchone()

    if not place:
        flash("❌ A hely nem található!", "danger")
        conn.close()
        return redirect(url_for("index"))

    if request.method == "POST":
        name = request.form["name"].strip()
        east = "{:.7f}".format(float(request.form["east"].strip()))
        north = "{:.7f}".format(float(request.form["north"].strip()))
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        error_messages = []

        # 🚨 Egyedi értékek ellenőrzése (kivéve a saját id-t)
        cursor.execute("SELECT id FROM places WHERE name = ? AND id != ?", (name, id))
        if cursor.fetchone():
            error_messages.append("⚠️ A név már létezik az adatbázisban!")

        cursor.execute("SELECT id FROM places WHERE east = ? AND id != ?", (east, id))
        if cursor.fetchone():
            error_messages.append("⚠️ A keleti koordináta már létezik az adatbázisban!")

        cursor.execute("SELECT id FROM places WHERE north = ? AND id != ?", (north, id))
        if cursor.fetchone():
            error_messages.append("⚠️ Az északi koordináta már létezik az adatbázisban!")

        # 📌 Ha van duplikált adat, figyelmeztetést adunk és nem mentünk el semmit
        if error_messages:
            for error in error_messages:
                flash(error, "warning")
            conn.close()
            return redirect(url_for("edit", id=id))

        try:
            cursor.execute("UPDATE places SET name = ?, east = ?, north = ?, address = ?, notes = ? WHERE id = ?",
                           (name, east, north, address, notes, id))
            conn.commit()
            flash("✅ A hely sikeresen módosítva!", "success")
        except sqlite3.IntegrityError:
            flash("⚠️ A módosítás sikertelen, mert az új adatok már léteznek!", "warning")
        finally:
            conn.close()

        return redirect(url_for("index"))

    conn.close()
    return render_template("edit.html", place=place)

@app.route("/export", endpoint="export_csv")
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

print("\n📌 Regisztrált Flask végpontok:")
print(app.url_map)  # 📌 Kiírja az összes elérhető Flask végpontot

if __name__ == "__main__":
    from waitress import serve
    import os

    port = int(os.environ.get("PORT", 5000))  # Railway által adott port
    serve(app, host="0.0.0.0", port=port)

from flask import Flask, jsonify, request
import sqlite3

app = Flask(__name__)

def get_places():
    """Lekérdezi az összes helyet az adatbázisból"""
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, latitude, longitude, address FROM places")
    rows = cursor.fetchall()
    conn.close()
    
    places = []
    for row in rows:
        places.append({
            "name": row[0],
            "latitude": row[1],
            "longitude": row[2],
            "address": row[3]
        })
    
    return places

@app.route('/api/places', methods=['GET'])
def api_places():
    """API végpont, ami JSON formátumban visszaadja a helyeket"""
    return jsonify(get_places())

if __name__ == '__main__':
    app.run(debug=True)


