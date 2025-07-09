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
from datetime import datetime, timedelta, UTC  # UTC használata
import firebase_admin
from firebase_admin import credentials, auth
from dotenv import load_dotenv
import json

# Naplózás beállítása (DEBUG szint engedélyezve)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Middleware az inaktivitási időzítéshez
class SessionTimeout:
    def __init__(self, app, timeout=timedelta(minutes=15), ping_timeout=timedelta(seconds=30)):
        self.app = app
        self.timeout = timeout
        self.ping_timeout = ping_timeout

    def __call__(self, environ, start_response):
        session_data = environ.get('flask.session', {})
        last_activity = session_data.get('last_activity')
        last_seen = session_data.get('last_seen', datetime.now(UTC))  # Timezone-aware

        if 'user' in session_data and last_activity:
            inactivity = datetime.now(UTC) - last_activity
            unseen = datetime.now(UTC) - last_seen
            logger.debug(f"Inactivity: {inactivity}, Unseen: {unseen}")  # Debug log
            if inactivity > self.timeout or unseen > self.ping_timeout:  # Csak valódi inaktivitás vagy ping hiánya
                session.pop('user', None)
                flash("⚠️ Inaktivitás miatt kijelentkeztél!", "warning")

        def new_start_response(status, headers, exc_info=None):
            if 'user' in session:
                session['last_activity'] = datetime.now(UTC)
                session['last_seen'] = datetime.now(UTC)  # Frissítjük az utolsó látott időt minden kérésnél
            return start_response(status, headers, exc_info)

        return self.app(environ, new_start_response)

app = Flask(__name__)
app.secret_key = "titkoskulcs"
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=2)  # Csökkentettük 2 percre
app.wsgi_app = SessionTimeout(app.wsgi_app, timeout=timedelta(minutes=15), ping_timeout=timedelta(seconds=30))

# Lokális .env betöltése, ha létezik (élesben felülírja a Railway változó)
load_dotenv()

# Firebase inicializálása környezetváltozóval
firebase_config = os.environ.get('FIREBASE_CONFIG')
if not firebase_config:
    logger.error("FIREBASE_CONFIG környezetváltozó nincs beállítva!")
    raise ValueError("FIREBASE_CONFIG környezetváltozó nincs beállítva!")

try:
    cred_data = json.loads(firebase_config)
    cred = credentials.Certificate(cred_data)
except json.JSONDecodeError as e:
    logger.error(f"Hiba a Firebase konfiguráció JSON dekódolásakor: {str(e)}")
    raise ValueError("A FIREBASE_CONFIG érvénytelen JSON formátumú!")
except Exception as e:
    logger.error(f"Hiba a Firebase inicializálása során: {str(e)}")
    raise

firebase_admin.initialize_app(cred)

# Adatbázis kapcsolat URL a környezetváltozóból
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL környezetváltozó nincs beállítva!")
    raise ValueError("DATABASE_URL környezetváltozó nincs beállítva!")

# Kapcsolat pool inicializálása
try:
    db_pool = psycopg2.pool.SimpleConnectionPool(
        minconn=1,
        maxconn=5,
        dsn=DATABASE_URL,
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
    attempt = 0
    while attempt < max_retries:
        try:
            conn = db_pool.getconn()
            conn.cursor_factory = psycopg2.extras.DictCursor
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

def release_db_connection(conn):
    try:
        db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"Hiba a kapcsolat visszahelyezése során: {str(e)}")

def is_valid_coordinate(value):
    return bool(re.match(r"^-?\d{1,2}(\.\d{1,7})?$", value))

@app.route("/")
def index():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM places ORDER BY name")
            places = cursor.fetchall()
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

@app.route("/add", methods=["GET", "POST"])
def add_place():
    if 'user' not in session:
        flash("⚠️ Kérlek, jelentkezz be a hely hozzáadásához!", "danger")
        return redirect(url_for("login"))
    if request.method == "POST":
        name = request.form["name"].strip()
        east = round(float(request.form["east"].strip()), 6)
        north = round(float(request.form["north"].strip()), 6)
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
                cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (%s, %s, %s, %s, %s)", (name, east, north, address, notes))
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
                            east = round(float(row.get("Kelet", "").strip()), 6)
                            north = round(float(row.get("Észak", "").strip()), 6)
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
                            cursor.execute("INSERT INTO places (name, east, north, address, notes) VALUES (%s, %s, %s, %s, %s)", (name, east, north, address, notes))
                            conn.commit()
                            imported_count += 1
                            logger.debug(f"Sikeres insert: id={cursor.lastrowid if hasattr(cursor, 'lastrowid') else 'N/A'}")
                        except psycopg2.Error as e:
                            logger.error(f"Hiba az insert során: {str(e)}, sor: {row}")
                            error_entries.append(f"Sor: {row} - Hiba: {str(e)}")
                            if conn:
                                conn.rollback()
                if imported_count > 0:
                    flash(f"✅ {imported_count} új hely importálva!", "success")
                if duplicate_entries:
                    flash(f"⚠️ {len(duplicate_entries)} duplikált bejegyzés nem importálódott: {', '.join(duplicate_entries)}", "warning")
                if error_entries:
                    flash(f"⚠️ {len(error_entries)} hiba történt az importálás során: {', '.join(error_entries)}", "warning")
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
        return redirect(url_for("index"))
    return render_template("import.html")

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
            east = round(float(request.form["east"].strip()), 6)
            north = round(float(request.form["north"].strip()), 6)
            address = request.form.get("address", "").strip()
            notes = request.form.get("notes", "").strip()
            if not (is_valid_coordinate(str(east)) and is_valid_coordinate(str(north))):
                flash("⚠️ Érvénytelen koordináta formátum!", "danger")
                return redirect(url_for("edit", id=id))
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
                cursor.execute("UPDATE places SET name = %s, east = %s, north = %s, address = %s, notes = %s WHERE id = %s", (name, east, north, address, notes, id))
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
    finally:
        if conn:
            release_db_connection(conn)
    return redirect(url_for("index"))

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
            user = auth.get_user_by_email(email)
            logger.debug(f"Felhasználó custom claims: {user.custom_claims}")
            role = user.custom_claims.get('role', 'user') if user.custom_claims else 'user'
            session['user'] = {'email': user.email, 'uid': user.uid, 'role': role}
            session.permanent = True
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

@app.route("/logout", methods=['POST'])
def logout():
    logger.debug("Logout endpoint called")
    if 'user' in session:
        session.pop('user', None)
        flash("✅ Sikeresen kijelentkeztél!", "success")
    return redirect(url_for("index"))

@app.route("/ping", methods=['GET'])
def ping():
    logger.debug("Ping received")
    if 'user' in session:
        session['last_seen'] = datetime.now(UTC)
    return '', 204  # No Content válasz

@app.route("/clear-sessions", methods=['GET'])
def clear_sessions():
    logger.debug("Clearing all sessions")
    session.clear()  # Törli az aktuális session-t
    return redirect(url_for("login"))

@app.route("/api/places", methods=["GET"])
def api_places():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM places ORDER BY name")
            places = cursor.fetchall()
        places_list = [{"id": place["id"], "name": place["name"], "east": place["east"], "north": place["north"], "address": place["address"], "notes": place["notes"]} for place in places]
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

@app.route("/users", methods=["GET"])
def users():
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként a felhasználók megtekintéséhez!", "danger")
        return redirect(url_for("login"))
    try:
        users = auth.list_users().iterate_all()
        user_list = [{"email": user.email, "role": user.custom_claims.get('role', 'user') if user.custom_claims else 'user', "uid": user.uid} for user in users]
        return render_template("users.html", users=user_list)
    except Exception as e:
        logger.error(f"Hiba a felhasználók lekérdezése során: {str(e)}")
        flash("⚠️ Hiba történt a felhasználók lekérdezése során!", "danger")
        return redirect(url_for("index"))

@app.route("/add_user", methods=["GET", "POST"])
def add_user():
    if 'user' not in session or session.get('user', {}).get('role') != 'admin':
        flash("⚠️ Kérlek, jelentkezz be adminisztrátorként új felhasználó hozzáadásához!", "danger")
        return redirect(url_for("login"))
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        role = request.form.get("role", "user")
        if not email or not password:
            flash("⚠️ Kérlek, add meg az email címet és a jelszót!", "danger")
            return redirect(url_for("add_user"))
        try:
            user = auth.create_user(email=email, password=password)
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
        flash(f"⚠️ Hiba történt a felhasználó törlése során: {str(e)}", "danger")
        return redirect(url_for("users"))

if __name__ == "__main__":
    print("\n📌 Regisztrált Flask végpontok:")
    print(app.url_map)
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Alkalmazás indítása a {port} porton...")
    serve(app, host="0.0.0.0", port=port)