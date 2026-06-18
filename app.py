from flask import Flask, render_template, request, redirect, url_for, session, flash
from database import get_db_connection
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Custom imports
from sendemailtemplate import sendemailnewregistration
import os

app = Flask(__name__)
app.secret_key = 'vivaah_vibes_secret_key_123'

# Ensure static/profile_pics directory exists
PROFILE_PICS_DIR = os.path.join(app.root_path, 'static', 'profile_pics')
os.makedirs(PROFILE_PICS_DIR, exist_ok=True)

# Initialize wedding rituals table if not exists
def init_rituals_table():
    conn = get_db_connection()
    conn.execute('''
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

init_rituals_table()

# Context processor to make active user's details available in templates
@app.context_processor
def inject_user():
    user = None
    if 'user_id' in session:
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE user_id = ?', (session['user_id'],)).fetchone()
        conn.close()
    return dict(current_user=user)

# Home route
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    role = session.get('role')
    if role == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif role == 'vendor':
        return redirect(url_for('vendor_dashboard'))
    else:
        return redirect(url_for('customer_dashboard'))

# Customer Dashboard
@app.route('/dashboard')
def customer_dashboard():
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))
    return render_template('index.html')

# Vendor Dashboard
@app.route('/vendor/dashboard')
def vendor_dashboard():
    if 'user_id' not in session or session.get('role') != 'vendor':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    vendor = conn.execute('SELECT * FROM vendors WHERE user_id = ?', (session['user_id'],)).fetchone()
    
    # Get booking requests logged for this vendor
    bookings = []
    if vendor:
        bookings = conn.execute('''
            SELECT b.*, u.full_name as customer_name, u.phone as customer_phone 
            FROM bookings b 
            JOIN users u ON b.customer_id = u.user_id 
            WHERE b.vendor_id = ?
        ''', (vendor['vendor_id'],)).fetchall()
    
    conn.close()
    return render_template('vendor_dashboard.html', vendor=vendor, bookings=bookings)

# Admin Dashboard
@app.route('/admin/dashboard')
def admin_dashboard():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    stats = {
        'total_customers': conn.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0],
        'total_vendors': conn.execute("SELECT COUNT(*) FROM users WHERE role = 'vendor'").fetchone()[0],
        'total_bookings': conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    }
    # List unverified vendors
    vendors = conn.execute('SELECT * FROM vendors WHERE is_verified = 0').fetchall()
    conn.close()
    return render_template('admin.html', stats=stats, vendors=vendors)

# Registration
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        full_name = request.form['full_name']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        role = request.form['role'] # 'customer' or 'vendor'
        city = request.form['city']
        
        # Vendor-specific inputs
        business_name = request.form.get('business_name')
        service_type = request.form.get('service_type')
        base_price = request.form.get('base_price')
        description = request.form.get('description')
        
        hashed_password = generate_password_hash(password)
        
        conn = get_db_connection()
        try:
            # Check if email exists
            existing = conn.execute('SELECT email FROM users WHERE email = ?', (email,)).fetchone()
            if existing:
                flash('Email already registered!', 'danger')
                return render_template('register.html')
            
            # Start database transaction
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (full_name, email, phone, password_hash, role, city)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (full_name, email, phone, hashed_password, role, city))
            
            user_id = cursor.lastrowid
            
            # If registration is as a vendor, save to vendor table
            if role == 'vendor':
                cursor.execute('''
                    INSERT INTO vendors (user_id, business_name, service_type, location, base_price, description, contact_info, is_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ''', (user_id, business_name, service_type, f"{city}, India", float(base_price or 0), description, phone))
            
            conn.commit()
            flash('Registration successful! You can now log in.', 'success')
            sendemailnewregistration(email,full_name)
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            flash(f'An error occurred: {str(e)}', 'danger')
        finally:
            conn.close()
            
    return render_template('register.html')

# Login
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['user_id']
            session['email'] = user['email']
            session['role'] = user['role']
            session['name'] = user['full_name']
            
            flash(f"Welcome back, {user['full_name']}!", 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password!', 'danger')
            
    return render_template('login.html')

# Logout
@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

# Budget Planner
@app.route('/budget', methods=['GET', 'POST'])
def budget_page():
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = get_db_connection()
    
    if request.method == 'POST':
        total_budget = float(request.form['total_budget'])
        wedding_date = request.form['wedding_date']
        selected_services = request.form.getlist('services')
        
        if not selected_services:
            flash('Please select at least one wedding service!', 'danger')
            conn.close()
            return redirect(url_for('budget_page'))
            
        # Point weights for allocation
        weights = {
            'venue': 50,
            'catering': 20,
            'dj': 15,
            'makeup': 15
        }
        
        # Calculate sum of selected weights
        total_weight = sum(weights[s] for s in selected_services)
        
        # Calculate allocated sums
        venue_alloc = (weights['venue'] / total_weight) * total_budget if 'venue' in selected_services else 0.0
        catering_alloc = (weights['catering'] / total_weight) * total_budget if 'catering' in selected_services else 0.0
        dj_alloc = (weights['dj'] / total_weight) * total_budget if 'dj' in selected_services else 0.0
        makeup_alloc = (weights['makeup'] / total_weight) * total_budget if 'makeup' in selected_services else 0.0
        
        # Save to database
        conn.execute('''
            INSERT INTO budgets (customer_id, total_budget, venue_allocated, catering_allocated, dj_allocated, makeup_allocated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                total_budget = excluded.total_budget,
                venue_allocated = excluded.venue_allocated,
                catering_allocated = excluded.catering_allocated,
                dj_allocated = excluded.dj_allocated,
                makeup_allocated = excluded.makeup_allocated
        ''', (user_id, total_budget, venue_alloc, catering_alloc, dj_alloc, makeup_alloc))
        
        conn.commit()
        session['wedding_date'] = wedding_date
        
        flash('Budget split calculated and saved successfully!', 'success')
        conn.close()
        return redirect(url_for('budget_page'))
        
    # GET: Load current budget details
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    conn.close()
    
    return render_template('budget.html', budget=budget)

# Wedding Rituals Route
@app.route('/rituals', methods=['GET', 'POST'])
def rituals_page():
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    conn = get_db_connection()
    
    if request.method == 'POST':
        haldi_date = request.form.get('haldi_date', '')
        mehendi_date = request.form.get('mehendi_date', '')
        sangeet_date = request.form.get('sangeet_date', '')
        wedding_date = request.form.get('wedding_date', '')
        
        # Save or update in database
        conn.execute('''
            INSERT INTO wedding_rituals (customer_id, haldi_date, mehendi_date, sangeet_date, wedding_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                haldi_date = excluded.haldi_date,
                mehendi_date = excluded.mehendi_date,
                sangeet_date = excluded.sangeet_date,
                wedding_date = excluded.wedding_date
        ''', (user_id, haldi_date, mehendi_date, sangeet_date, wedding_date))
        conn.commit()
        
        # Keep wedding date in session if provided
        if wedding_date:
            session['wedding_date'] = wedding_date
            
        flash('Wedding rituals schedule updated successfully!', 'success')
        conn.close()
        return redirect(url_for('rituals_page'))
        
    # GET: Fetch rituals schedule
    rituals = conn.execute('SELECT * FROM wedding_rituals WHERE customer_id = ?', (user_id,)).fetchone()
    conn.close()
    return render_template('rituals.html', rituals=rituals)

# Browse & Search Vendors
@app.route('/vendors')
def search_vendors():
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))
        
    category = request.args.get('type', 'venue')
    selected_date = request.args.get('date', session.get('wedding_date', ''))
    guest_count = int(request.args.get('guests', 0) or 0)
    
    user_id = session['user_id']
    conn = get_db_connection()
    
    # 1. Fetch user's budget allocations
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    
    # Get default limit based on category
    default_limit = None
    if budget:
        if category == 'venue':
            default_limit = budget['venue_allocated']
        elif category == 'catering':
            default_limit = budget['catering_allocated']
        elif category == 'dj':
            default_limit = budget['dj_allocated']
        elif category == 'makeup':
            default_limit = budget['makeup_allocated']
            
    # Read user budget limit filter override or default
    budget_limit = request.args.get('budget_limit')
    if budget_limit == '' or budget_limit is None:
        budget_limit = default_limit
    else:
        budget_limit = float(budget_limit)
        
    # If no budget limit exists in DB and user didn't specify one, set to a high number
    if budget_limit is None or budget_limit == 0:
        budget_limit = 99999999.0
        
    # 2. Query matching vendors with double-booking checks and average ratings
    query = '''
        SELECT v.*, AVG(r.rating) as avg_rating, COUNT(r.rating) as review_count
        FROM vendors v
        LEFT JOIN reviews r ON v.vendor_id = r.vendor_id
        WHERE v.service_type = ? 
          AND v.is_verified = 1
          AND v.base_price <= ?
          AND (v.capacity IS NULL OR v.capacity >= ?)
          AND v.vendor_id NOT IN (
              SELECT vendor_id FROM bookings 
              WHERE wedding_date = ?
          )
        GROUP BY v.vendor_id
    '''
    
    vendors = conn.execute(query, (category, budget_limit, guest_count, selected_date)).fetchall()
    conn.close()
    
    filters = {
        'date': selected_date,
        'guests': guest_count,
        'budget_limit': budget_limit if budget_limit != 99999999.0 else ''
    }
    
    return render_template('vendors.html', 
                           category=category, 
                           vendors=vendors, 
                           filters=filters,
                           default_limit=default_limit)

# Connect & Log Booking interest
@app.route('/connect/<int:vendor_id>', methods=['POST'])
def connect_vendor(vendor_id):
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))
        
    wedding_date = request.form['wedding_date']
    agreed_price = float(request.form['agreed_price'])
    customer_id = session['user_id']
    
    if not wedding_date:
        flash('Wedding date is required to connect with a vendor!', 'danger')
        return redirect(url_for('customer_dashboard'))
        
    conn = get_db_connection()
    
    # 1. Double check availability to avoid race conditions
    already_booked = conn.execute('''
        SELECT * FROM bookings 
        WHERE vendor_id = ? AND wedding_date = ?
    ''', (vendor_id, wedding_date)).fetchone()
    
    if already_booked:
        flash('Sorry, this vendor was just booked by someone else on that date!', 'danger')
        conn.close()
        return redirect(url_for('customer_dashboard'))
        
    # 2. Insert into bookings log
    conn.execute('''
        INSERT INTO bookings (customer_id, vendor_id, wedding_date, agreed_price, status)
        VALUES (?, ?, ?, ?, 'contacted')
    ''', (customer_id, vendor_id, wedding_date, agreed_price))
    conn.commit()
    conn.close()
    
    flash('Vendor booking log saved! Direct contact details unlocked in your profile.', 'success')
    return redirect(url_for('profile'))

# Profile
@app.route('/profile')
def profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    conn = get_db_connection()
    
    # Fetch user's budget details if set
    budget = conn.execute('SELECT * FROM budgets WHERE customer_id = ?', (user_id,)).fetchone()
    
    # Fetch contacted vendors list
    bookings = conn.execute('''
        SELECT b.*, v.business_name, v.service_type, v.contact_info, v.location 
        FROM bookings b 
        JOIN vendors v ON b.vendor_id = v.vendor_id 
        WHERE b.customer_id = ?
    ''', (user_id,)).fetchall()
    
    conn.close()
    return render_template('profile.html', budget=budget, bookings=bookings)

# Profile Photo Upload Handler
@app.route('/upload_profile_pic', methods=['POST'])
def upload_profile_pic():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    if 'profile_pic' not in request.files:
        flash('No file provided!', 'danger')
        return redirect(url_for('profile'))
        
    file = request.files['profile_pic']
    if file.filename == '':
        flash('No selected file!', 'danger')
        return redirect(url_for('profile'))
        
    if file:
        filename = secure_filename(f"user_{session['user_id']}_{file.filename}")
        file_path = os.path.join(app.root_path, 'static', 'profile_pics', filename)
        file.save(file_path)
        
        # Save relative URL to database
        pic_url = f"/static/profile_pics/{filename}"
        
        conn = get_db_connection()
        conn.execute('UPDATE users SET profile_pic_url = ? WHERE user_id = ?', (pic_url, session['user_id']))
        conn.commit()
        conn.close()
        
        flash('Profile picture uploaded successfully!', 'success')
        
    return redirect(url_for('profile'))

# Review & Rating Submission
@app.route('/submit_review/<int:vendor_id>', methods=['POST'])
def submit_review(vendor_id):
    if 'user_id' not in session or session.get('role') != 'customer':
        return redirect(url_for('login'))
        
    rating = int(request.form['rating'])
    comment = request.form['comment']
    customer_id = session['user_id']
    
    conn = get_db_connection()
    conn.execute('''
        INSERT INTO reviews (customer_id, vendor_id, rating, comment)
        VALUES (?, ?, ?, ?)
    ''', (customer_id, vendor_id, rating, comment))
    conn.commit()
    conn.close()
    
    flash('Thank you for your rating and review!', 'success')
    return redirect(url_for('profile'))

# Admin Verify Vendor Account
@app.route('/admin/verify/<int:vendor_id>', methods=['POST'])
def verify_vendor(vendor_id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    conn.execute('UPDATE vendors SET is_verified = 1 WHERE vendor_id = ?', (vendor_id,))
    conn.commit()
    conn.close()
    
    flash('Vendor account approved successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

# Help
@app.route('/help')
def help_page():
    return render_template('help.html')

if __name__ == '__main__':
    app.run(debug=True)
