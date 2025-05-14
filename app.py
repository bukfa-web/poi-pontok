import logging
from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify
import psycopg2
import psycopg2.extras
import psycopg2.pool
import re
import os
import csv
from waitress import serve

# Naplózás beállítása (INFO és ERROR szintek)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "titkoskulcs"

# Adatbázis kapcsolat URL a környezetváltozóból
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    logger.error("DATABASE_URL környezetváltozó nincs beállítva!")
    raise ValueError("DATABASE_URL környezetváltozó nincs beállítva!")

# Kapcsolat pool inicializálása
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,  # Minimális kapcsolatok száma
        maxconn=10,  # Maximális kapcsolatok száma
        dsn=DATABASE_URL
    )
    logger.info("Kapcsolat pool sikeresen inicializálva.")
except Exception as e:
    logger.error(f"Hiba a kapcsolat pool inicializálása során: {str(e)}")
    raise

# Kapcsolat lekérése a poolból
def get_db_connection():
    """ Kapcsolat lekérése a poolból """
    try:
        conn = db_pool.getconn()
        conn.cursor_factory = psycopg2.extras.DictCursor
        # Keepalive beállítások a kapcsolat stabilizálására
        conn.set_session(autocommit=False)
        return conn
    except psycopg2.OperationalError as e:
        logger.warning(f"Kapcsolódási hiba a poolból: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Hiba a kapcsolat lekérése során: {str(e)}")
        raise

# Kapcsolat visszahelyezése a poolba
def release_db_connection(conn):
    """ Kapcsolat visszahelyezése a poolba """
    try:
        db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"Hiba a kapcsolat visszahelyezése során: {str(e)}")

# Koordináta formátum ellenőrzés
def is_valid_coordinate(value):
    """ Ellenőrzi, hogy egy szám megfelelő koordináta formátum-e """
    return bool(re.match(r"^-?\d{1,2}(\.\d{1,7})?$", value))

# Főoldal
@app.route("/")
def index():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM places ORDER BY name")
            places = cursor.fetchall()
        return render_template("index.html", places=places)
    except psycopg2.OperationalError as e:
        logger.error(f"Kapcsolati hiba a főoldal lekérdezése során: {str(e)}")
        flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
        return render_template("index.html", places=[])
    except Exception as e:
        logger.error(f"Hiba a főoldal lekérdezése során: {str(e)}")
        flash(f"⚠️ Hiba történt: {str(e)}", "danger")
        return render_template("index.html", places=[])
    finally:
        if conn:
            release_db_connection(conn)

# Új hely hozzáadása
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
            logger.warning("Érvénytelen koordináta formátum.")
            return redirect(url_for("add_place"))

        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO places (name, east, north, address, notes) VALUES (%s, %s, %s, %s, %s)",
                    (name, east, north, address, notes)
                )
            conn.commit()
            flash("✅ Hely sikeresen hozzáadva!", "success")
        except psycopg2.errors.UniqueViolation:
            flash("⚠️ Ez a hely már létezik!", "warning")
        except psycopg2.OperationalError as e:
            logger.error(f"Kapcsolati hiba az új hely hozzáadása során: {str(e)}")
            flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
        except Exception as e:
            logger.error(f"Hiba történt az adatbázis művelet során: {str(e)}")
            flash(f"⚠️ Hiba történt: {str(e)}", "danger")
        finally:
            if conn:
                release_db_connection(conn)

        return redirect(url_for("index"))

    return render_template("add.html")

# CSV importálás
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

        imported_count = 0
        duplicate_entries = []
        conn = None

        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
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
                            cursor.execute(
                                "INSERT INTO places (name, east, north, address, notes) VALUES (%s, %s, %s, %s, %s)",
                                (name, east, north, address, notes)
                            )
                            imported_count += 1
                        except psycopg2.errors.UniqueViolation:
                            duplicate_entries.append(name)
            conn.commit()
        except psycopg2.OperationalError as e:
            logger.error(f"Kapcsolati hiba a CSV importálás során: {str(e)}")
            flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
        except Exception as e:
            logger.error(f"Hiba történt a CSV importálás során: {str(e)}")
            flash(f"⚠️ Hiba történt az importálás során: {str(e)}", "danger")
        finally:
            if conn:
                release_db_connection(conn)
            os.remove(file_path)

        if imported_count > 0:
            flash(f"✅ {imported_count} új hely importálva!", "success")
        if duplicate_entries:
            flash(f"⚠️ {len(duplicate_entries)} bejegyzés már létezett!", "warning")

        return redirect(url_for("index"))

    return render_template("import.html")

# Hely törlése
@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM places WHERE id = %s", (id,))
        conn.commit()
        flash("🗑️ Hely sikeresen törölve!", "success")
    except psycopg2.OperationalError as e:
        logger.error(f"Kapcsolati hiba a hely törlése során: {str(e)}")
        flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
    except Exception as e:
        logger.error(f"Hiba történt a hely törlése során: {str(e)}")
        flash(f"⚠️ Hiba történt: {str(e)}", "danger")
    finally:
        if conn:
            release_db_connection(conn)

    return redirect(url_for("index"))

# Hely szerkesztése
@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit(id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM places WHERE id = %s", (id,))
            place = cursor.fetchone()

        if not place:
            flash("❌ A hely nem található!", "danger")
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

            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE places SET name = %s, east = %s, north = %s, address = %s, notes = %s WHERE id = %s",
                    (name, east, north, address, notes, id)
                )
            conn.commit()
            flash("✅ A hely sikeresen módosítva!", "success")
            return redirect(url_for("index"))

        return render_template("edit.html", place=place)
    except psycopg2.OperationalError as e:
        logger.error(f"Kapcsolati hiba a hely szerkesztése során: {str(e)}")
        flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
        return redirect(url_for("index"))
    except Exception as e:
        logger.error(f"Hiba történt a hely szerkesztése során: {str(e)}")
        flash(f"⚠️ Hiba történt: {str(e)}", "danger")
        return redirect(url_for("index"))
    finally:
        if conn:
            release_db_connection(conn)

# CSV exportálás
@app.route("/export")
def export_csv():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT name, east, north, address, notes FROM places")
            places = cursor.fetchall()

        csv_data = "Név,Kelet,Észak,Cím,Megjegyzések\n"
        for place in places:
            csv_data += f"{place['name']},{place['east']},{place['north']},{place['address']},{place['notes']}\n"

        response = Response(csv_data.encode("utf-8-sig"), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=helyek_export.csv"
        return response
    except psycopg2.OperationalError as e:
        logger.error(f"Kapcsolati hiba a CSV exportálás során: {str(e)}")
        flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
        return redirect(url_for("index"))
    except Exception as e:
        logger.error(f"Hiba történt a CSV exportálás során: {str(e)}")
        flash(f"⚠️ Hiba történt: {str(e)}", "danger")
        return redirect(url_for("index"))
    finally:
        if conn:
            release_db_connection(conn)

# API végpont az összes hely JSON-ként való lekérdezésére
@app.route("/api/places", methods=["GET"])
def api_places():
    """API végpont az összes hely listázására JSON formátumban."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM places ORDER BY name")
            places = cursor.fetchall()

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
    except psycopg2.OperationalError as e:
        logger.error(f"Kapcsolati hiba az API lekérdezés során: {str(e)}")
        return jsonify({"error": "Adatbázis kapcsolati hiba, kérlek próbáld újra később!"}), 500
    except Exception as e:
        logger.error(f"Hiba történt az API lekérdezés során: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            release_db_connection(conn)

if __name__ == "__main__":
    print("\n📌 Regisztrált Flask végpontok:")
    print(app.url_map)
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Alkalmazás indítása a {port} porton...")
    serve(app, host="0.0.0.0", port=port)
