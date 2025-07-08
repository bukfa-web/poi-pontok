import firebase_admin
from firebase_admin import credentials, auth

# Inicializáld a Firebase-t
cred = credentials.Certificate("firebase-adminsdk.json")
firebase_admin.initialize_app(cred)

# Admin szerepkör beállítása egy felhasználóhoz
def set_admin_role(email):
    try:
        user = auth.get_user_by_email(email)
        auth.set_custom_user_claims(user.uid, {"role": "admin"})
        print(f"Admin szerepkör sikeresen beállítva a {email} felhasználónak.")
    except Exception as e:
        print(f"Hiba az admin szerepkör beállítása során: {str(e)}")

if __name__ == "__main__":
    admin_email = "bukfa6406@gmail.com"  # Az új felhasználó email címe
    set_admin_role(admin_email)