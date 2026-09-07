import unittest
import os
import sqlite3
from app import app
from database import get_db_connection, init_db, seed_db
from helpers import compute_booking_financials, calculate_cancellation_refund, check_vendor_availability, log_audit

class WeddingUtsavTestSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        app.config['TESTING'] = True
        init_db()
        seed_db()

    def setUp(self):
        self.client = app.test_client()
        conn = get_db_connection()
        conn.execute("DELETE FROM users WHERE email LIKE '%@test.com'")
        conn.execute("DELETE FROM vendors WHERE user_id NOT IN (SELECT user_id FROM users)")
        conn.execute("DELETE FROM bookings WHERE customer_id NOT IN (SELECT user_id FROM users)")
        conn.execute("DELETE FROM vendor_availability WHERE reason LIKE '%test%'")
        conn.commit()
        conn.close()

    def get_user_session(self, email, password):
        self.client.get('/logout')
        return self.client.post('/login', data={'email': email, 'password': password}, follow_redirects=True)

    # 1. RBAC & Backend Authorization Tests
    def test_rbac_unauthenticated(self):
        res = self.client.get('/dashboard', follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/login', res.location)

    def test_rbac_customer_denied_admin(self):
        self.client.post('/register', data={
            'full_name': 'Test Customer', 'email': 'cust_rbac@test.com',
            'phone': '1234567890', 'city': 'Kolhapur', 'password': 'password123', 'role': 'customer'
        })
        self.get_user_session('cust_rbac@test.com', 'password123')
        
        res = self.client.get('/admin/dashboard')
        self.assertEqual(res.status_code, 403)

    def test_rbac_vendor_denied_admin(self):
        self.get_user_session('vendor1@vivaahvibes.com', 'vendor123')
        res = self.client.get('/admin/dashboard')
        self.assertEqual(res.status_code, 403)

    # 2. Vendor Verification System Tests
    def test_vendor_verification_workflow(self):
        # Register new vendor
        self.client.post('/register', data={
            'full_name': 'New Vendor', 'email': 'newvendor@test.com', 'phone': '9876500000',
            'city': 'Kolhapur', 'password': 'password123', 'role': 'vendor',
            'business_name': 'New Grand Venue', 'service_type': 'venue',
            'base_price': '50000', 'description': 'Grand hall'
        })
        
        conn = get_db_connection()
        vendor = conn.execute('SELECT * FROM vendors WHERE business_name = "New Grand Venue"').fetchone()
        conn.close()
        
        # All newly registered vendors start as PENDING
        self.assertIsNotNone(vendor)
        self.assertEqual(vendor['verification_status'], 'PENDING')
        self.assertEqual(vendor['is_verified'], 0)

        # Admin Login & Approval
        self.get_user_session('admin@vivaahvibes.com', 'admin123')
        res = self.client.post(f'/admin/verify/{vendor["vendor_id"]}', data={'action': 'approve', 'notes': 'Looks great'}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        updated_vendor = conn.execute('SELECT * FROM vendors WHERE vendor_id = ?', (vendor['vendor_id'],)).fetchone()
        conn.close()
        self.assertEqual(updated_vendor['verification_status'], 'VERIFIED')
        self.assertEqual(updated_vendor['is_verified'], 1)

    # 3. Financial Calculation Engine (20% Advance & 5% Platform Commission)
    def test_financial_calculations(self):
        # Base price ₹80,000, 10% discount = ₹8,000 => Final ₹72,000
        financials = compute_booking_financials(base_price=80000, discount_type='percentage', discount_value=10)
        self.assertEqual(financials['original_price'], 80000.0)
        self.assertEqual(financials['discount_amount'], 8000.0)
        self.assertEqual(financials['final_price'], 72000.0)
        self.assertEqual(financials['advance_required'], 14400.0) # 20% of 72,000
        self.assertEqual(financials['commission_amount'], 3600.0) # 5% of 72,000
        self.assertEqual(financials['vendor_earnings'], 68400.0) # 72,000 - 3,600

    # 4. Double-Booking Prevention Test (Backend Check & DB-Level Partial Unique Constraint)
    def test_double_booking_prevention(self):
        conn = get_db_connection()
        vendor = conn.execute('SELECT vendor_id FROM vendors WHERE business_name = "Royal Palace Lawn & Hall"').fetchone()
        v_id = vendor['vendor_id']
        test_date = '2027-01-01'
        
        # Delete any existing test bookings for this date
        conn.execute('DELETE FROM bookings WHERE vendor_id = ? AND wedding_date = ?', (v_id, test_date))
        conn.commit()

        # Check initial availability
        is_avail, _ = check_vendor_availability(conn, v_id, test_date)
        self.assertTrue(is_avail)

        # Insert a confirmed booking on test_date
        conn.execute('''
            INSERT INTO bookings (customer_id, vendor_id, wedding_date, agreed_price, final_price, status)
            VALUES (1, ?, ?, 50000, 50000, 'CONFIRMED')
        ''', (v_id, test_date))
        conn.commit()

        # Check availability again -> MUST be False
        is_avail, reason = check_vendor_availability(conn, v_id, test_date)
        conn.close()
        self.assertFalse(is_avail)
        self.assertIn("already booked", reason)

    def test_database_level_double_booking_prevention(self):
        """Verifies that SQLite partial unique index constraint raises IntegrityError if race condition occurs."""
        import sqlite3
        conn = get_db_connection()
        vendor = conn.execute('SELECT vendor_id FROM vendors WHERE business_name = "Royal Palace Lawn & Hall"').fetchone()
        v_id = vendor['vendor_id']
        test_date = '2027-06-01'
        
        conn.execute('DELETE FROM bookings WHERE vendor_id = ? AND wedding_date = ?', (v_id, test_date))
        conn.commit()

        # Insert first active booking
        conn.execute('''
            INSERT INTO bookings (customer_id, vendor_id, wedding_date, agreed_price, final_price, status)
            VALUES (1, ?, ?, 50000, 50000, 'PENDING')
        ''', (v_id, test_date))
        conn.commit()

        # Attempt to insert second active booking at database engine level -> MUST raise IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('''
                INSERT INTO bookings (customer_id, vendor_id, wedding_date, agreed_price, final_price, status)
                VALUES (2, ?, ?, 50000, 50000, 'CONFIRMED')
            ''', (v_id, test_date))
            conn.commit()
            
        conn.close()

    # 5. Full Booking Lifecycle & Payment Sandbox Test
    def test_full_booking_lifecycle(self):
        # Create Customer
        self.client.post('/register', data={
            'full_name': 'Flow Customer', 'email': 'flow@test.com', 'phone': '1112223333',
            'city': 'Sangli', 'password': 'password123', 'role': 'customer'
        })
        self.get_user_session('flow@test.com', 'password123')
        
        conn = get_db_connection()
        vendor = conn.execute('SELECT v.vendor_id, v.user_id, u.email FROM vendors v JOIN users u ON v.user_id = u.user_id WHERE u.email NOT LIKE "%@test.com" AND v.verification_status = "VERIFIED" ORDER BY v.vendor_id ASC LIMIT 1').fetchone()
        conn.close()
        
        test_date = '2027-02-14'
        
        # 1. Customer Requests Booking -> Status PENDING
        self.client.post(f'/connect/{vendor["vendor_id"]}', data={'wedding_date': test_date}, follow_redirects=True)
        
        conn = get_db_connection()
        booking = conn.execute('SELECT * FROM bookings WHERE customer_id = (SELECT user_id FROM users WHERE email="flow@test.com")').fetchone()
        conn.close()
        
        self.assertIsNotNone(booking)
        self.assertEqual(booking['status'], 'PENDING')

        # 2. Vendor Accepts Request -> Status PAYMENT_PENDING
        self.get_user_session(vendor['email'], 'vendor123')
        resp = self.client.post(f'/booking/{booking["booking_id"]}/respond', data={'action': 'accept'}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        
        conn = get_db_connection()
        booking = conn.execute('SELECT * FROM bookings WHERE booking_id = ?', (booking['booking_id'],)).fetchone()
        conn.close()
        self.assertEqual(booking['status'], 'PAYMENT_PENDING')

        # 3. Customer Pays 20% Advance -> Status CONFIRMED
        self.get_user_session('flow@test.com', 'password123')
        self.client.post(f'/booking/{booking["booking_id"]}/pay', data={'payment_method': 'UPI'}, follow_redirects=True)
        
        conn = get_db_connection()
        booking = conn.execute('SELECT * FROM bookings WHERE booking_id = ?', (booking['booking_id'],)).fetchone()
        payment = conn.execute('SELECT * FROM payments WHERE booking_id = ?', (booking['booking_id'],)).fetchone()
        commission = conn.execute('SELECT * FROM commissions WHERE booking_id = ?', (booking['booking_id'],)).fetchone()
        conn.close()

        self.assertEqual(booking['status'], 'CONFIRMED')
        self.assertGreater(booking['advance_paid'], 0.0)
        self.assertEqual(payment['status'], 'SUCCESS')
        self.assertIsNotNone(commission)
        self.assertEqual(commission['commission_amount'], round(booking['final_price'] * 0.05, 2))

    # 6. Customer Soft Delete Test
    def test_customer_soft_delete(self):
        self.client.post('/register', data={
            'full_name': 'Soft Delete User', 'email': 'softdel@test.com', 'phone': '9988776655',
            'city': 'Karad', 'password': 'password123', 'role': 'customer'
        })
        self.get_user_session('softdel@test.com', 'password123')
        
        res = self.client.post('/profile/delete_account', follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        conn = get_db_connection()
        user = conn.execute('SELECT status FROM users WHERE email = "softdel@test.com"').fetchone()
        conn.close()
        self.assertEqual(user['status'], 'deleted')

        # Attempting login now must fail
        login_res = self.get_user_session('softdel@test.com', 'password123')
        self.assertIn(b'deleted', login_res.data)

    # 7. Audit Log Recording Test
    def test_audit_logs(self):
        log_audit(1, 'TEST_AUDIT_ACTION', 'test', 1, "Test details")
        conn = get_db_connection()
        logs = conn.execute('SELECT * FROM audit_logs WHERE action = "TEST_AUDIT_ACTION"').fetchall()
        conn.close()
        self.assertGreater(len(logs), 0)
        self.assertEqual(logs[0]['action'], 'TEST_AUDIT_ACTION')

if __name__ == '__main__':
    unittest.main()
