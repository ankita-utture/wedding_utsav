import sqlite3
import os
from werkzeug.security import generate_password_hash

DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vivaah_vibes.db')

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Create Users Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('customer', 'vendor', 'admin')),
        profile_pic_url TEXT,
        city TEXT
    )
    ''')
    
    # 2. Create Vendors Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS vendors (
        vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        business_name TEXT NOT NULL,
        service_type TEXT NOT NULL CHECK(service_type IN ('venue', 'catering', 'dj', 'makeup')),
        location TEXT NOT NULL,
        capacity INTEGER, -- only relevant for venue
        base_price REAL NOT NULL, -- price per day/event/plate
        description TEXT,
        contact_info TEXT NOT NULL,
        image_url TEXT,
        is_verified INTEGER DEFAULT 0, -- 0 for pending, 1 for verified
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    ''')
    
    # 3. Create Bookings Table (Direct Contact Logs)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        vendor_id INTEGER,
        wedding_date TEXT NOT NULL,
        agreed_price REAL NOT NULL,
        status TEXT DEFAULT 'contacted',
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE,
        FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE CASCADE
    )
    ''')
    
    # 4. Create Reviews Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reviews (
        review_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        vendor_id INTEGER,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        comment TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE,
        FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE CASCADE
    )
    ''')
    
    # 5. Create Budgets Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS budgets (
        budget_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER UNIQUE,
        total_budget REAL NOT NULL,
        venue_allocated REAL DEFAULT 0,
        catering_allocated REAL DEFAULT 0,
        dj_allocated REAL DEFAULT 0,
        makeup_allocated REAL DEFAULT 0,
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    ''')
    
    conn.commit()
    conn.close()
    print("Database tables initialized successfully.")

def seed_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if database is already seeded (e.g. if we already have users)
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        print("Database already seeded. Skipping...")
        conn.close()
        return

    # 1. Create default admin account
    admin_password = generate_password_hash("admin123")
    cursor.execute('''
    INSERT INTO users (full_name, email, phone, password_hash, role, city)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', ("System Admin", "admin@vivaahvibes.com", "9999999999", admin_password, "admin", "Kolhapur"))
    
    # 2. Create some vendor users and list their services
    mock_vendors = [
        # Venues
        {
            "name": "Royal Palace Lawn & Hall",
            "type": "venue",
            "location": "Tarabai Park, Kolhapur",
            "capacity": 800,
            "price": 180000,
            "phone": "9876543210",
            "desc": "A luxurious royal palace feel hall with open lawn. Perfect for grand traditional weddings.",
            "img": "/static/images/venue1.jpg"
        },
        {
            "name": "Gokul Garden & Banquet",
            "type": "venue",
            "location": "Sangli Road, Miraj",
            "capacity": 400,
            "price": 95000,
            "phone": "9876543211",
            "desc": "Elegant AC banquet hall with an attached green lawn, highly accessible and pocket-friendly.",
            "img": "/static/images/venue2.jpg"
        },
        {
            "name": "Shanti Heritage Lawns",
            "type": "venue",
            "location": "Karad Bypass, Karad",
            "capacity": 1200,
            "price": 250000,
            "phone": "9876543212",
            "desc": "Spacious premium lawns accommodating massive guest lists. Highly decorative themes available.",
            "img": "/static/images/venue3.jpg"
        },
        # Catering
        {
            "name": "Maharaja Shahi Caterers",
            "type": "catering",
            "location": "Shahupuri, Kolhapur",
            "capacity": None,
            "price": 450, # Price per plate
            "phone": "9876543213",
            "desc": "Authentic Maharashtrian, Punjabi, and Chinese cuisines. Famous for Kolhapuri Veg & Non-Veg specialties.",
            "img": "/static/images/catering1.jpg"
        },
        {
            "name": "Shree Annapurna Pure Veg",
            "type": "catering",
            "location": "Vishrambag, Sangli",
            "capacity": None,
            "price": 280,
            "phone": "9876543214",
            "desc": "Pure vegetarian catering with high hygienic standards. Multi-cuisine starter counters available.",
            "img": "/static/images/catering2.jpg"
        },
        {
            "name": "Spice Route Feast",
            "type": "catering",
            "location": "Satara Road, Satara",
            "capacity": None,
            "price": 600,
            "phone": "9876543215",
            "desc": "Gourmet buffet styles, custom live stations (Chaats, Pasta, Mocktails) for modern premium weddings.",
            "img": "/static/images/catering3.jpg"
        },
        # DJs
        {
            "name": "DJ Ramesh Sound & Lights",
            "type": "dj",
            "location": "Rajarampuri, Kolhapur",
            "capacity": None,
            "price": 22000,
            "phone": "9876543216",
            "desc": "High bass dual setup, decorative LED lights, and custom sound system for traditional Baraat and Sangeet.",
            "img": "/static/images/dj1.jpg"
        },
        {
            "name": "Bass Boosters DJ Group",
            "type": "dj",
            "location": "Station Road, Sangli",
            "capacity": None,
            "price": 40000,
            "phone": "9876543217",
            "desc": "Professional concert-level sound setups, smoke machines, intelligent lighting systems, and live mixing DJs.",
            "img": "/static/images/dj2.jpg"
        },
        {
            "name": "Sur Melody Beats DJ",
            "type": "dj",
            "location": "Peth Naka, Karad",
            "capacity": None,
            "price": 15000,
            "phone": "9876543218",
            "desc": "Affordable budget-friendly sound systems, perfect for Haldi, Mehendi, and intimate indoor gatherings.",
            "img": "/static/images/dj3.jpg"
        },
        # Makeup
        {
            "name": "Suhani Bridal Studio",
            "type": "makeup",
            "location": "Tarabai Park, Kolhapur",
            "capacity": None,
            "price": 18000,
            "phone": "9876543219",
            "desc": "Specialist in HD Bridal makeup, hair styling, saree draping. Uses top-quality international brands.",
            "img": "/static/images/makeup1.jpg"
        },
        {
            "name": "Gloss & Glow Makeovers",
            "type": "makeup",
            "location": "Gaonbhag, Sangli",
            "capacity": None,
            "price": 30000,
            "phone": "9876543220",
            "desc": "Airbrush makeup specialists, providing modern, minimalist and premium looks for pre-wedding and main event.",
            "img": "/static/images/makeup2.jpg"
        },
        {
            "name": "Elegant Touch Salon & Academy",
            "type": "makeup",
            "location": "Near Bus Stand, Karad",
            "capacity": None,
            "price": 10000,
            "phone": "9876543221",
            "desc": "Very budget-friendly bridal packages, offering good makeup, Mehendi artist bookings, and bridesmaid facials.",
            "img": "/static/images/makeup3.jpg"
        }
    ]

    for index, v in enumerate(mock_vendors):
        email = f"vendor{index+1}@vivaahvibes.com"
        pwd = generate_password_hash("vendor123")
        
        # Insert user account for vendor
        cursor.execute('''
        INSERT INTO users (full_name, email, phone, password_hash, role, city)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (v["name"], email, v["phone"], pwd, "vendor", v["location"].split(",")[-1].strip()))
        
        user_id = cursor.lastrowid
        
        # Insert vendor details
        cursor.execute('''
        INSERT INTO vendors (user_id, business_name, service_type, location, capacity, base_price, description, contact_info, image_url, is_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, v["name"], v["type"], v["location"], v["capacity"], v["price"], v["desc"], v["phone"], v["img"], 1))

    conn.commit()
    conn.close()
    print("Database seeded with default Admin and 12 mock vendors successfully.")

if __name__ == '__main__':
    init_db()
    seed_db()
