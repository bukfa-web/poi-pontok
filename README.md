# POI Pontok - Helyek Listája Projekt

## Leírás
Ez egy Flask alapú webalkalmazás, amely helyek (POI - Points of Interest) kezelését teszi lehetővé. Funkciók:
- Helyek hozzáadása, szerkesztése, törlése (koordinátákkal, címmel, megjegyzéssel).
- CSV fájlból importálás és exportálás.
- Keresés a listában.
- Felhasználó autentikáció Firebase-el (bejelentkezés, kijelentkezés, admin szerepkörök).
- Admin funkciók: Felhasználók kezelése (hozzáadás, szerkesztés, törlés).
- Sötét téma Bootstrap-pel, reszponzív design.

Az adatbázis PostgreSQL (pl. Railway deployment-hez), a frontend HTML/JS/CSS sablonokkal.

## Telepítés és futtatás lokálisan
### Előfeltételek
- Python 3.10+ telepítve.
- Git telepítve.
- Környezetváltozók: 
  - `DATABASE_URL`: PostgreSQL kapcsolat string (pl. `postgresql://user:pass@host/db`).
  - `FIREBASE_CONFIG`: Firebase admin SDK JSON string-ként (lásd app.py).
