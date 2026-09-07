import sqlite3
import os
from werkzeug.security import generate_password_hash

DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vivaah_vibes.db')

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_column_names(cursor, table_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [column[1] for column in cursor.fetchall()]

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Create/Ensure Users Table
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
    
    # Check and add new columns to users if missing
    user_cols = get_column_names(cursor, 'users')
    if 'status' not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'deleted'))")
    if 'created_at' not in user_cols:
        cursor.execute("ALTER TABLE users ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
    
    # 2. Create/Ensure Vendors Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS vendors (
        vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        business_name TEXT NOT NULL,
        service_type TEXT NOT NULL CHECK(service_type IN ('venue', 'catering', 'dj', 'makeup')),
        location TEXT NOT NULL,
        capacity INTEGER,
        base_price REAL NOT NULL,
        description TEXT,
        contact_info TEXT NOT NULL,
        image_url TEXT,
        is_verified INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    ''')
    
    # Check and add new columns to vendors if missing
    vendor_cols = get_column_names(cursor, 'vendors')
    if 'verification_status' not in vendor_cols:
        cursor.execute("ALTER TABLE vendors ADD COLUMN verification_status TEXT DEFAULT 'PENDING' CHECK(verification_status IN ('PENDING', 'VERIFIED', 'REJECTED', 'CHANGES_REQUESTED', 'SUSPENDED', 'DEACTIVATED'))")
    if 'rejection_reason' not in vendor_cols:
        cursor.execute("ALTER TABLE vendors ADD COLUMN rejection_reason TEXT")
    if 'discount_type' not in vendor_cols:
        cursor.execute("ALTER TABLE vendors ADD COLUMN discount_type TEXT DEFAULT 'none' CHECK(discount_type IN ('none', 'percentage', 'fixed'))")
    if 'discount_value' not in vendor_cols:
        cursor.execute("ALTER TABLE vendors ADD COLUMN discount_value REAL DEFAULT 0.0")

    # Migrate legacy is_verified -> verification_status
    cursor.execute("UPDATE vendors SET verification_status = 'VERIFIED' WHERE is_verified = 1 AND (verification_status IS NULL OR verification_status = 'PENDING')")
    cursor.execute("UPDATE vendors SET is_verified = 1 WHERE verification_status = 'VERIFIED'")
    cursor.execute("UPDATE vendors SET is_verified = 0 WHERE verification_status != 'VERIFIED'")
    
    # 3. Create/Ensure Bookings Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        vendor_id INTEGER,
        wedding_date TEXT NOT NULL,
        agreed_price REAL NOT NULL,
        status TEXT DEFAULT 'PENDING',
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE,
        FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE CASCADE
    )
    ''')
    
    # Check and add snapshot & financial columns to bookings if missing
    booking_cols = get_column_names(cursor, 'bookings')
    if 'original_price' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN original_price REAL DEFAULT 0.0")
    if 'discount_amount' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN discount_amount REAL DEFAULT 0.0")
    if 'final_price' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN final_price REAL DEFAULT 0.0")
    if 'advance_required' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN advance_required REAL DEFAULT 0.0")
    if 'advance_paid' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN advance_paid REAL DEFAULT 0.0")
    if 'remaining_amount' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN remaining_amount REAL DEFAULT 0.0")
    if 'commission_rate' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN commission_rate REAL DEFAULT 0.05")
    if 'commission_amount' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN commission_amount REAL DEFAULT 0.0")
    if 'vendor_earnings' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN vendor_earnings REAL DEFAULT 0.0")
    if 'cancelled_by' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN cancelled_by TEXT")
    if 'cancellation_reason' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN cancellation_reason TEXT")
    if 'cancellation_time' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN cancellation_time DATETIME")
    if 'refund_amount' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN refund_amount REAL DEFAULT 0.0")
    if 'created_at' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
    if 'updated_at' not in booking_cols:
        cursor.execute("ALTER TABLE bookings ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP")

    # Migrate legacy status values in bookings
    cursor.execute("UPDATE bookings SET status = 'CONFIRMED' WHERE status = 'contacted'")
    
    # Partial Unique Index to enforce DB-level double-booking prevention on active bookings
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_active_vendor_booking 
        ON bookings (vendor_id, wedding_date) 
        WHERE status IN ('PENDING', 'ACCEPTED', 'PAYMENT_PENDING', 'CONFIRMED')
    ''')
    
    # Backfill missing snapshot values for legacy bookings
    cursor.execute("SELECT booking_id, agreed_price, status FROM bookings WHERE final_price = 0.0 OR final_price IS NULL")
    legacy_bookings = cursor.fetchall()
    for bk in legacy_bookings:
        price = bk['agreed_price'] or 0.0
        adv_req = round(price * 0.20, 2)
        comm = round(price * 0.05, 2)
        v_earn = round(price - comm, 2)
        adv_paid = adv_req if bk['status'] in ('CONFIRMED', 'COMPLETED') else 0.0
        rem = round(price - adv_paid, 2)
        cursor.execute('''
            UPDATE bookings 
            SET original_price = ?,
                discount_amount = 0.0,
                final_price = ?,
                advance_required = ?,
                advance_paid = ?,
                remaining_amount = ?,
                commission_rate = 0.05,
                commission_amount = ?,
                vendor_earnings = ?
            WHERE booking_id = ?
        ''', (price, price, adv_req, adv_paid, rem, comm, v_earn, bk['booking_id']))

    # 4. Create Reviews Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reviews (
        review_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        vendor_id INTEGER,
        booking_id INTEGER,
        rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
        comment TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE,
        FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE CASCADE,
        FOREIGN KEY (booking_id) REFERENCES bookings (booking_id) ON DELETE CASCADE
    )
    ''')
    review_cols = get_column_names(cursor, 'reviews')
    if 'booking_id' not in review_cols:
        cursor.execute("ALTER TABLE reviews ADD COLUMN booking_id INTEGER")
    
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
    
    # 6. Create Vendor Availability Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS vendor_availability (
        availability_id INTEGER PRIMARY KEY AUTOINCREMENT,
        vendor_id INTEGER NOT NULL,
        blocked_date TEXT NOT NULL,
        reason TEXT DEFAULT 'Blocked by vendor',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(vendor_id, blocked_date),
        FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE CASCADE
    )
    ''')
    
    # 7. Create Payments Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS payments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        payment_type TEXT DEFAULT 'ADVANCE' CHECK(payment_type IN ('ADVANCE', 'FULL', 'REMAINING')),
        status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'SUCCESS', 'FAILED', 'REFUNDED', 'PARTIALLY_REFUNDED')),
        transaction_reference TEXT UNIQUE,
        payment_gateway TEXT DEFAULT 'MockGateway',
        paid_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (booking_id) REFERENCES bookings (booking_id) ON DELETE CASCADE,
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    ''')
    
    # 8. Create Commissions Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS commissions (
        commission_id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER UNIQUE NOT NULL,
        vendor_id INTEGER NOT NULL,
        gross_amount REAL NOT NULL,
        commission_rate REAL DEFAULT 0.05,
        commission_amount REAL NOT NULL,
        vendor_earnings REAL NOT NULL,
        status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'EARNED', 'REFUNDED', 'CANCELLED')),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (booking_id) REFERENCES bookings (booking_id) ON DELETE CASCADE,
        FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE CASCADE
    )
    ''')

    # 9. Create Audit Logs Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS audit_logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id INTEGER,
        details TEXT,
        ip_address TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE SET NULL
    )
    ''')
    
    # 10. Create Wedding Rituals Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS wedding_rituals (
        ritual_id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER UNIQUE,
        haldi_date TEXT,
        mehendi_date TEXT,
        sangeet_date TEXT,
        wedding_date TEXT,
        FOREIGN KEY (customer_id) REFERENCES users (user_id) ON DELETE CASCADE
    )
    ''')
    
    conn.commit()
    conn.close()
    print("Database schema initialized and upgraded successfully.")

def seed_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] > 0:
        print("Database already seeded. Skipping...")
        conn.close()
        return

    # 1. Create default admin account
    admin_password = generate_password_hash("admin123")
    cursor.execute('''
    INSERT INTO users (full_name, email, phone, password_hash, role, city, status)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ("System Admin", "admin@vivaahvibes.com", "9999999999", admin_password, "admin", "Kolhapur", "active"))
    
    # 2. Create mock vendor users and list their services
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
            "price": 450,
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
        
        cursor.execute('''
        INSERT INTO users (full_name, email, phone, password_hash, role, city, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (v["name"], email, v["phone"], pwd, "vendor", v["location"].split(",")[-1].strip(), "active"))
        
        user_id = cursor.lastrowid
        
        # Existing demo vendors are set to VERIFIED for backwards compatibility
        cursor.execute('''
        INSERT INTO vendors (user_id, business_name, service_type, location, capacity, base_price, description, contact_info, image_url, is_verified, verification_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'VERIFIED')
        ''', (user_id, v["name"], v["type"], v["location"], v["capacity"], v["price"], v["desc"], v["phone"], v["img"]))

    conn.commit()
    conn.close()
    print("Database seeded with default Admin and 12 mock vendors successfully.")

if __name__ == '__main__':
    init_db()
    seed_db()
