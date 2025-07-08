import logging
from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify, session
import psycopg2
import psycopg2.extras
import psycopg2.pool
import re
import os
import csv
import time
from waitress import serve
from datetime import timedelta
import firebase_admin
from firebase_admin import credentials, auth

# Naplózás beállítása (DEBUG szint engedélyezve)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "titkoskulcs"

# Munkamenet konfigurálása
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=30)  # Munkamenet 30 perc után lejár

# Firebase inicializálása
cred = credentials.Certificate("firebase-adminsdk.json")
firebase_admin.initialize_app(cred)

# Adatbázis kapcsolat URL a környezetváltozóból
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    logger.error("DATABASE_URL környezetváltozó nincs beállítva!")
    raise ValueError("DATABASE_URL környezetváltozó nincs beállítva!")

# Kapcsolat pool inicializálása
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,  # Minimális kapcsolatok száma
        maxconn=5,  # Maximális kapcsolatok számának csökkentése
        dsn=DATABASE_URL,
        # Keepalive beállítások
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5
    )
    logger.info("Kapcsolat pool sikeresen inicializálva.")
except Exception as e:
    logger.error(f"Hiba a kapcsolat pool inicializálása során: {str(e)}")
    raise

# Kapcsolat lekérése a poolból újracsatlakozási logikával
def get_db_connection(max_retries=3, retry_delay=1):
    """ Kapcsolat lekérése a poolból """
    attempt = 0
    while attempt < max_retries:
        try:
            conn = db_pool.getconn()
            conn.cursor_factory = psycopg2.extras.DictCursor
            # Teszteljük a kapcsolatot egy egyszerű lekérdezéssel
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            logger.debug("Kapcsolat sikeresen lekérve a poolból.")
            return conn
        except psycopg2.OperationalError as e:
            logger.warning(f"Kapcsolódási hiba (próbálkozás {attempt + 1}/{max_retries}): {str(e)}")
            attempt += 1
            if attempt == max_retries:
                logger.error(f"Kapcsolódás sikertelen {max_retries} próbálkozás után: {str(e)}")
                raise
            time.sleep(retry_delay)
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
        # A remotepg és is_admin változók átadása a sablonnak
        is_admin = session.get('user', {}).get('role') == 'admin'
        remotepg = 'user' in session
        logger.debug(f"Sesszió adatok: {session.get('user')}, is_admin: {is_admin}")
        return render_template("index.html", places=places, remotepg=remotepg, is_admin=is_admin)
    except psycopg2.OperationalError as e:
        logger.error(f"Kapcsolati hiba a főoldal lekérdezése során: {str(e)}")
        flash("⚠️ Adatbázis kapcsolati hiba, kérlek próbáld újra később!", "danger")
        return render_template("index.html", places=[], remotepg=False, is_admin=False)
    except Exception as e:
        logger.error(f"Hiba a főoldal lekérdezése során: {str(e)}")
        flash(f"⚠️ Hiba történt: {str(e)}", "danger")
        return render_template("index.html", places=[], remotepg=False, is_admin=False)
    finally:
        if conn:
            release_db_connection(conn)

# Új hely hozzáadása
@app.route("/add", methods=["GET", "POST"])
def add_place():
    if 'user' not in session:
        flash("⚠️ Kérlek, jelentkezz be a hely hozzáadásához!", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        name = request.form["name"].strip()
        east = round(float(request.form["east"].strip()), 6)  # Kerekítés 6 tizedesjegyre
        north = round(float(request.form["north"].strip()), 6)  # Kerekítés 6 tizedesjegyre
        address = request.form.get("address", "").strip()
        notes = request.form.get("notes", "").strip()

        if not (is_valid_coordinate(str(east)) and is_valid_coordinate(str(north))):
            flash("⚠️ Érvénytelen koordináta formátum!", "danger")
            logger.warning("Érvénytelen koordináta formátum.")
            return redirect(url_for("add_place"))

        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                # Ellenőrizzük az egyediséget minden mezőre az adatbázisban
                cursor.execute("SELECT 1 FROM places WHERE name = %s", (name,))
                if cursor.fetchone():
                    flash("⚠️ Ez a név már létezik! Kérlek, válassz egyedi nevet.", "warning")
                    return redirect(url_for("add_place"))

                cursor.execute("SELECT 1 FROM places WHERE east = %s", (east,))
                if cursor.fetchone():
                    flash("⚠️ Ez a Kelet koordináta már létezik! Kérlek, válassz egyedi koordinátát.", "warning")
                    return redirect(url_for("add_place"))

                cursor.execute("SELECT 1 FROM places WHERE north = %s", (north,))
                if cursor.fetchone():
                    flash("⚠️ Ez az Észak koordináta már létezik! Kérlek, válassz egyedi koordinátát.", "warning")
                    return redirect(url_for("add_place"))

                # Ha minden mező egyedi, mentsük az adatot
                cursor.execute(
                    "INSERT INTO places (name, east, north, address, notes) VALUES (%s, %s, %s, %s, %s)",
                    (name, east, north, address, notes)
                )
            conn.commit()
            flash("✅ Hely sikeresen hozzáadva!", "success")
        except psycopg2.errors.UniqueViolation as e:
            logger.error(f"Egyediség megsértése: {str(e)}")
            flash("⚠️ Ez a hely már létezik (mezők egyediek kell legyenek)!", "warning")
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
    if 'user' not in session:
        flash("⚠️ Kérlek, jelentkezz be a CSV importálásához!", "danger")
        return redirect(url_for("login"))

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
        error_entries = []
        conn = None

        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                logger.debug(f"Importálás megkezdése fájlból: {file_path}")
                with open(file_path, newline="", encoding="utf-8-sig") as csvfile:
                    reader = csv.DictReader(csvfile)
                    logger.debug(f"CSV fejléc: {reader.fieldnames}")
                    for row in reader:
                        logger.debug(f"Feldolgozás alatt lévő sor: {row}")
                        name = row.get("Név", "").strip()
                        try:
                            east = round(float(row.get("Kelet", "").strip()), 6)  # Kerekítés 6 tizedesjegyre
                            north = round(float(row.get("Észak", "").strip()), 6)  # Kerekítés 6 tizedesjegyre
                        except ValueError as ve:
                            logger.warning(f"Érvénytelen koordináta formátum a sorban: {row}, hiba: {str(ve)}")
                            error_entries.append(f"Sor: {row} - Érvénytelen koordináta")
                            continue

                        address = row.get("Cím", "").strip()
                        notes = row.get("Megjegyzések", "").strip()

                        if not (is_valid_coordinate(str(east)) and is_valid_coordinate(str(north))):
                            logger.warning(f"Érvénytelen koordináta: east={east}, north={north}")
                            error_entries.append(f"Sor: {row} - Érvénytelen koordináta formátum")
                            continue

                        # Ellenőrizzük az egyediséget minden mezőre
                        cursor.execute("SELECT 1 FROM places WHERE name = %s", (name,))
                        if cursor.fetchone():
                            logger.warning(f"Duplikált név: {name}")
                            duplicate_entries.append(f"Név: {name}")
                            continue

                        cursor.execute("SELECT 1 FROM places WHERE east = %s", (east,))
                        if cursor.fetchone():
                            logger.warning(f"Duplikált Kelet: {east}")
                            duplicate_entries.append(f"Kelet: {east}")
                            continue

                        cursor.execute("SELECT 1 FROM places WHERE north = %s", (north,))
                        if cursor.fetchone():
                            logger.warning(f"Duplikált Észak: {north}")
                            duplicate_entries.append(f"Észak: {north}")
                            continue

                        try:
                            logger.debug(f"Insert készítése: name={name}, east={east}, north={north}")
                            cursor.execute(
                                "INSERT INTO places (name, east, north, address, notes) VALUES (%s, %s, %s, %s, %s)",
                                (name, east, north, address, notes)
                            )
                            conn.commit()  # Egyenként commit-oljuk a sikeres insert-eket
                            imported_count += 1
                            logger.debug(f"Sikeres insert: id={cursor.lastrowid if hasattr(cursor, 'lastrowid') else 'N/A'}")
                        except psycopg2.Error as e:
                            logger.error(f"Hiba az insert során: {str(e)}, sor: {row}")
                            error_entries.append(f"Sor: {row} - Hiba: {str(e)}")
                            if conn:
                                conn.rollback()  # Hiba esetén visszavonjuk az adott műveletet
        except psycopg2.Error as e:
            logger.error(f"Tranzakciós hiba a CSV importálás során: {str(e)}")
            flash(f"⚠️ Tranzakciós hiba az importálás során: {str(e)}", "danger")
        except Exception as e:
            logger.error(f"Váratlan hiba a CSV importálás során: {str(e)}")
            flash(f"⚠️ Váratlan hiba az importálás során: {str(e)}", "danger")
        finally:
            if conn:
                release_db_connection(conn)
            os.remove(file_path)

        if imported_count > 0:
            flash(f"✅ {imported_count} új hely importálva!", "success")
        if duplicate_entries:
            flash(f"⚠️ {len(duplicate_entries)} duplikált bejegyzés nem importálódott: {', '.join(duplicate_entries)}", "warning")
        if error_entries:
            flash(f"⚠️ {len(error_entries)} hiba történt az importálás során: {', '.join(error_entries)}", "warning")

        return redirect(url_for("index"))

    return render_template("import.html")

# Hely törlése
@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként a hely törléséhez!", "danger")
        return redirect(url_for("login"))

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            logger.debug(f"Próbálom törölni a helyet id={id}")
            cursor.execute("DELETE FROM places WHERE id = %s", (id,))
            logger.debug(f"Törlés eredménye: {cursor.rowcount} sor érintett")
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
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként a hely szerkesztéséhez!", "danger")
        return redirect(url_for("login"))

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            logger.debug(f"Próbálom lekérdezni a helyet id={id}")
            cursor.execute("SELECT * FROM places WHERE id = %s", (id,))
            place = cursor.fetchone()
            logger.debug(f"Lekérdezett hely: {place}")

        if not place:
            flash("❌ A hely nem található!", "danger")
            return redirect(url_for("index"))

        if request.method == "POST":
            name = request.form["name"].strip()
            east = round(float(request.form["east"].strip()), 6)  # Kerekítés 6 tizedesjegyre
            north = round(float(request.form["north"].strip()), 6)  # Kerekítés 6 tizedesjegyre
            address = request.form.get("address", "").strip()
            notes = request.form.get("notes", "").strip()

            if not (is_valid_coordinate(str(east)) and is_valid_coordinate(str(north))):
                flash("⚠️ Érvénytelen koordináta formátum!", "danger")
                return redirect(url_for("edit", id=id))

            # Ellenőrizzük az egyediséget, kivéve a saját rekordot
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM places WHERE name = %s AND id != %s", (name, id))
                if cursor.fetchone():
                    flash("⚠️ Ez a név már létezik egy másik rekordban!", "warning")
                    return redirect(url_for("edit", id=id))

                cursor.execute("SELECT 1 FROM places WHERE east = %s AND id != %s", (east, id))
                if cursor.fetchone():
                    flash("⚠️ Ez a Kelet koordináta már létezik egy másik rekordban!", "warning")
                    return redirect(url_for("edit", id=id))

                cursor.execute("SELECT 1 FROM places WHERE north = %s AND id != %s", (north, id))
                if cursor.fetchone():
                    flash("⚠️ Ez az Észak koordináta már létezik egy másik rekordban!", "warning")
                    return redirect(url_for("edit", id=id))

                cursor.execute(
                    "UPDATE places SET name = %s, east = %s, north = %s, address = %s, notes = %s WHERE id = %s",
                    (name, east, north, address, notes, id)
                )
            conn.commit()
            flash("✅ A hely sikeresen módosítva!", "success")
            return redirect(url_for("index"))

        return render_template("edit.html", place=place, form_data=request.form if request.method == "POST" else None)
    except psycopg2.errors.UniqueViolation as e:
        logger.error(f"Egyediség megsértése: {str(e)}")
        flash("⚠️ Ez a hely már létezik (mezők egyediek kell legyenek)!", "warning")
        return redirect(url_for("edit", id=id))
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
    if 'user' not in session:
        flash("⚠️ Kérlek, jelentkezz be a CSV exportálásához!", "danger")
        return redirect(url_for("login"))

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

# Bejelentkezés
@app.route("/login", methods=["GET", "POST"])
def login():
    if 'user' in session:
        flash("Már be vagy jelentkezve!", "info")
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        if not email or not password:
            flash("⚠️ Kérlek, add meg az email címet és a jelszót!", "danger")
            return redirect(url_for("login"))

        try:
            # Firebase Authentication használata a hitelesítéshez
            user = auth.get_user_by_email(email)
            # Custom claims lekérdezése
            logger.debug(f"Felhasználó custom claims: {user.custom_claims}")
            role = user.custom_claims.get('role', 'user') if user.custom_claims else 'user'

            # Munkamenet beállítása
            session['user'] = {
                'email': user.email,
                'uid': user.uid,
                'role': role
            }
            session.permanent = True  # Munkamenet lejárati ideje az app.config alapján
            flash("✅ Bejelentkezés sikeres!", "success")
            return redirect(url_for("index"))

        except auth.UserNotFoundError:
            logger.warning(f"Nem létező felhasználó próbált bejelentkezni: {email}")
            flash("⚠️ Hibás email cím vagy jelszó!", "danger")
            return redirect(url_for("login"))
        except Exception as e:
            logger.error(f"Hiba a bejelentkezés során: {str(e)}")
            flash("⚠️ Hiba történt a bejelentkezés során!", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

# Kijelentkezés
@app.route("/logout")
def logout():
    if 'user' in session:
        session.pop('user', None)
        flash("✅ Sikeresen kijelentkeztél!", "success")
    return redirect(url_for("index"))

# API végpont az összes hely JSON-ként való lekérdezésére
@app.route("/api/places", methods=["GET"])
def api_places():
    """API végpont az összes hely listázásra JSON formátumban."""
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

# Felhasználók listázása
@app.route("/users", methods=["GET"])
def users():
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként a felhasználók megtekintéséhez!", "danger")
        return redirect(url_for("login"))

    conn = None
    try:
        # Firebase-ből lekérjük a felhasználókat
        users = auth.list_users().iterate_all()
        user_list = []
        for user in users:
            user_data = {
                'email': user.email,
                'role': user.custom_claims.get('role', 'user') if user.custom_claims else 'user',
                'uid': user.uid
            }
            user_list.append(user_data)

        return render_template("users.html", users=user_list)
    except Exception as e:
        logger.error(f"Hiba a felhasználók lekérdezése során: {str(e)}")
        flash("⚠️ Hiba történt a felhasználók lekérdezése során!", "danger")
        return redirect(url_for("index"))
    finally:
        if conn:
            release_db_connection(conn)

# Új felhasználó hozzáadása
@app.route("/add_user", methods=["GET", "POST"])
def add_user():
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként új felhasználó hozzáadásához!", "danger")
        return redirect(url_for("login"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        role = request.form.get("role", "user")  # Alapértelmezett szerepkör: 'user'

        if not email or not password:
            flash("⚠️ Kérlek, add meg az email címet és a jelszót!", "danger")
            return redirect(url_for("add_user"))

        try:
            # Új felhasználó létrehozása a Firebase-ben
            user = auth.create_user(
                email=email,
                password=password
            )
            # Szerepkör beállítása custom claim-ekkel
            auth.set_custom_user_claims(user.uid, {"role": role})
            logger.info(f"Új felhasználó létrehozva: email={email}, role={role}")
            flash("✅ Új felhasználó sikeresen létrehozva!", "success")
            return redirect(url_for("users"))
        except auth.EmailAlreadyExistsError:
            logger.warning(f"Már létezik felhasználó ezzel az emaillel: {email}")
            flash("⚠️ Ez az email cím már regisztrálva van!", "danger")
            return redirect(url_for("add_user"))
        except Exception as e:
            logger.error(f"Hiba az új felhasználó létrehozása során: {str(e)}")
            flash("⚠️ Hiba történt az új felhasználó létrehozása során!", "danger")
            return redirect(url_for("add_user"))

    return render_template("add_user.html")

# Felhasználó szerkesztése
@app.route("/edit_user/<uid>", methods=["GET", "POST"])
def edit_user(uid):
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként a felhasználó szerkesztéséhez!", "danger")
        return redirect(url_for("login"))

    try:
        user = auth.get_user(uid)
        if request.method == "POST":
            role = request.form.get("role", "user")
            auth.set_custom_user_claims(uid, {"role": role})
            logger.info(f"Felhasználó szerkesztve: email={user.email}, új szerepkör={role}")
            flash("✅ Felhasználó sikeresen szerkesztve!", "success")
            return redirect(url_for("users"))

        return render_template("edit_user.html", user=user)
    except auth.UserNotFoundError:
        logger.error(f"Nem létező felhasználó szerkesztése: uid={uid}")
        flash("⚠️ A felhasználó nem található!", "danger")
        return redirect(url_for("users"))
    except Exception as e:
        logger.error(f"Hiba a felhasználó szerkesztése során: {str(e)}")
        flash("⚠️ Hiba történt a felhasználó szerkesztése során!", "danger")
        return redirect(url_for("users"))

# Felhasználó törlése
@app.route("/delete_user/<uid>", methods=["POST"])
def delete_user(uid):
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként a felhasználó törléséhez!", "danger")
        return redirect(url_for("login"))

    try:
        auth.delete_user(uid)
        logger.info(f"Felhasználó törölve: uid={uid}")
        flash("✅ Felhasználó sikeresen törölve!", "success")
        return redirect(url_for("users"))
    except auth.UserNotFoundError:
        logger.error(f"Nem létező felhasználó törlése: uid={uid}")
        flash("⚠️ A felhasználó nem található!", "danger")
        return redirect(url_for("users"))
    except Exception as e:
        logger.error(f"Hiba a felhasználó törlése során: {str(e)}")
        flash("⚠️ Hiba történt a felhasználó törlése során!", "danger")
        return redirect(url_for("users"))

if __name__ == "__main__":
    print("\n📌 Regisztrált Flask végpontok:")
    print(app.url_map)
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Alkalmazás indítása a {port} porton...")
    serve(app, host="0.0.0.0", port=port)