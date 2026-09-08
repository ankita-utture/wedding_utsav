
import sqlite3
from werkzeug.security import generate_password_hash
import getpass

DB_NAME = "vivaah_vibes.db"

email = input("Enter your admin email: ").strip()
password = getpass.getpass("Enter your admin password: ")
confirm_password = getpass.getpass("Confirm your admin password: ")

if password != confirm_password:
    print("❌ Passwords do not match.")
    exit()

if not email or not password:
    print("❌ Email and password are required.")
    exit()

conn = sqlite3.connect(DB_NAME)

# Check whether this email already exists
existing_user = conn.execute(
    "SELECT user_id, role FROM users WHERE email = ?",
    (email,)
).fetchone()

if existing_user:
    print(f"❌ This email already exists with role: {existing_user[1]}")
    conn.close()
    exit()

# Hash the password before storing it
password_hash = generate_password_hash(password)

conn.execute("""
    INSERT INTO users
    (full_name, email, phone, password_hash, role, city, status)
    VALUES (?, ?, ?, ?, 'admin', ?, 'active')
""", (
    "Wedding Utsav Admin",
    email,
    "",
    password_hash,
    "Kolhapur"
))

conn.commit()
conn.close()

print("\n✅ Admin account created successfully!")
print("You can now log in using the normal Login page.")
print("Your password is stored as a secure hash.")

