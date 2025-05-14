import webbrowser
from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify
import psycopg2
import psycopg2.extras
import re
import os
import csv
import sys

app = Flask(__name__)
app.secret_key = "titkoskulcs"

# ✅ Adatbázis kapcsolat URL a környezetváltozóból
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL környezetváltozó nincs beállítva!")

# ✅ Adatbázis kapcsolat
def get_db_connection():
    """ Adatbázis kapcsolat létrehozása PostgreSQL-hez """
    conn = psycopg2.connect(DATABASE_URL)
    # A psycopg2-vel a DictCursor használatával hasonlóan működik, mint az sqlite3.Row
    conn.cursor_factory = psycopg2.extras.DictCursor
    return conn

# ✅ Koordináta formátum ellenőrzés
def is_valid_coordinate(value):
    """ Ellenőrzi, hogy egy szám megfelelő koordináta formátum-e """
    return bool(re.match(r"^-?\d{1,2}(\.\d{1,7})?$", value))

# ✅ Főoldal
@app.route("/")
def index():
    conn = get_db_connection()
    places = conn.execute("SELECT * FROM places ORDER BY name").fetchall()
    conn.close()
    return render_template("index.html", places=places)

# ✅ Új hely hozzáadása
@app.route("/add", methods=["GET", "POST"])
def add_place():
    if request.method == "POST":
        name = request.form["name"].strip()
        east = request.form["east"].strip()
        north = request.form["north"].strip()
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        if not (is_valid_coordinate(east) and is_valid_coordinate(north)):
            flash("⚠️ Érvénytelen koordináta formátum!", "danger")
            return redirect(url_for("add_place"))

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (?, ?, ?, ?, ?)", 
                           (name, east, north, address, notes))
            conn.commit()
            flash("✅ Hely sikeresen hozzáadva!", "success")
        except sqlite3.IntegrityError:
            flash("⚠️ Ez a hely már létezik!", "warning")
        finally:
            conn.close()

        return redirect(url_for("index"))

    return render_template("add.html")

# ✅ CSV importálás
@app.route("/import", methods=["GET", "POST"])
def import_csv():
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
            for row in reader:
                name = row.get("Név", "").strip()
                east = row.get("Kelet", "").strip()
                north = row.get("Észak", "").strip()
                address = row.get("Cím", "").strip()
                notes = row.get("Megjegyzések", "").strip()

                if not (is_valid_coordinate(east) and is_valid_coordinate(north)):
                    continue

                try:
                    cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (?, ?, ?, ?, ?)", 
                                   (name, east, north, address, notes))
                    imported_count += 1
                except sqlite3.IntegrityError:
                    duplicate_entries.append(name)

        conn.commit()
        conn.close()
        os.remove(file_path)

        if imported_count > 0:
            flash(f"✅ {imported_count} új hely importálva!", "success")
        if duplicate_entries:
            flash(f"⚠️ {len(duplicate_entries)} bejegyzés már létezett!", "warning")

        return redirect(url_for("index"))

    return render_template("import.html")

# ✅ Hely törlése
@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM places WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    flash("🗑️ Hely sikeresen törölve!", "success")
    return redirect(url_for("index"))

# ✅ Hely szerkesztése
@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM places WHERE id = ?", (id,))
    place = cursor.fetchone()

    if not place:
        flash("❌ A hely nem található!", "danger")
        conn.close()
        return redirect(url_for("index"))

    if request.method == "POST":
        name = request.form["name"].strip()
        east = request.form["east"].strip()
        north = request.form["north"].strip()
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        if not (is_valid_coordinate(east) and is_valid_coordinate(north)):
            flash("⚠️ Érvénytelen koordináta formátum!", "danger")
            return redirect(url_for("edit", id=id))

        cursor.execute("UPDATE places SET name = ?, east = ?, north = ?, address = ?, notes = ? WHERE id = ?",
                       (name, east, north, address, notes, id))
        conn.commit()
        flash("✅ A hely sikeresen módosítva!", "success")

    conn.close()
    return render_template("edit.html", place=place)

# ✅ CSV exportálás
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

print("\n📌 Regisztrált Flask végpontok:")
print(app.url_map)

# 🆕 API végpont az összes hely JSON-ként való lekérdezésére
@app.route("/api/places", methods=["GET"])
def api_places():
    """API végpont az összes hely listázására JSON formátumban."""
    conn = get_db_connection()
    places = conn.execute("SELECT * FROM places ORDER BY name").fetchall()
    conn.close()

    # JSON-válasz létrehozása
    places_list = []
    for place in places:
        places_list.append({
            "id": place["id"],
            "name": place["name"],
            "east": place["east"],
            "north": place["north"],
            "address": place["address"],
            "notes": place["notes"]
        })
    
    return jsonify(places_list)

if __name__ == "__main__":
    from waitress import serve
    port = int(os.environ.get("PORT", 5000))
    serve(app, host="0.0.0.0", port=port)
