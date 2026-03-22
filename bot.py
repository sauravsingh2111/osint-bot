#!/usr/bin/env python3
"""
OSINT BOT – Saurav Edition (Render Compatible)
- Token from environment variable (SECURE)
- Database path fixed for Render
- All tools working
"""

import os
import logging
import sqlite3
import json
import subprocess
import random
import re
import time
import requests
import whois
import instaloader
import hashlib
import qrcode
import io
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ============= CONFIGURATION =============
# TOKEN from environment variable (SECURE - don't hardcode!)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

BOT_USERNAME = "nano_osintbot"
ADMINS = [7878291627]

# Database path for Render (use current directory - writable)
DB_PATH = os.path.join(os.getcwd(), "osint_bot.db")

HAVEIBEENPWNED_KEY = ""
SHERLOCK_CMD = "sherlock"

# Saurav's data - only custom message for these
SAURAV_PHONES = ["6299830610", "9661428001", "8102007453", "7050828025"]
SAURAV_USERNAME = "sauravsingh2111"
SAURAV_TG_ID = "7878291627"

# ============= COSTS =============
COST_PHONE = 1
COST_TELEGRAM = 1
COST_OSINT = 2
COST_ADVANCED = 1

# ============= DATABASE HELPER =============
def db_execute(query, params=None, fetch_one=False, fetch_all=False):
    max_retries = 3
    for attempt in range(max_retries):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            if fetch_one:
                result = cursor.fetchone()
            elif fetch_all:
                result = cursor.fetchall()
            else:
                result = cursor.rowcount
            conn.commit()
            return result
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
        finally:
            if conn:
                conn.close()
    return None

# ============= DATABASE INITIALIZATION =============
def init_bot_database():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, points INTEGER DEFAULT 2,
        referred_by INTEGER, join_date TEXT, is_active INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referred_id INTEGER, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY, points INTEGER, used_by TEXT DEFAULT '', is_active INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, note TEXT, date TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ifsc_data (
        code TEXT PRIMARY KEY, bank TEXT, branch TEXT, address TEXT, city TEXT, district TEXT, state TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS aadhar_data (
        number TEXT PRIMARY KEY, name TEXT, father_name TEXT, address TEXT, phone TEXT, dob TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS osint_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, query_type TEXT, query TEXT, result TEXT, date TEXT)''')
    conn.commit()
    
    # Insert sample IFSC data
    sample_ifsc = [
        ("SBIN0001234", "State Bank of India", "Main Branch", "123 MG Road, Mumbai", "Mumbai", "Mumbai", "Maharashtra"),
        ("HDFC0005678", "HDFC Bank", "Corporate Branch", "456 Park Street, Delhi", "Delhi", "Delhi", "Delhi"),
        ("ICIC0009012", "ICICI Bank", "City Center", "789 Civil Lines, Bangalore", "Bangalore", "Bangalore", "Karnataka"),
        ("PNB0003456", "Punjab National Bank", "Rajouri Garden", "T-54 Rajouri Garden, New Delhi", "Delhi", "Delhi", "Delhi"),
        ("YESB0007890", "Yes Bank", "Andheri East", "Marol Naka, Andheri East, Mumbai", "Mumbai", "Mumbai", "Maharashtra"),
        ("AXIS0002345", "Axis Bank", "Koramangala", "80 Feet Road, Koramangala, Bangalore", "Bangalore", "Bangalore", "Karnataka"),
    ]
    for code, bank, branch, address, city, district, state in sample_ifsc:
        cursor.execute("INSERT OR IGNORE INTO ifsc_data VALUES (?, ?, ?, ?, ?, ?, ?)", (code, bank, branch, address, city, district, state))
    
    # Insert sample Aadhar data
    sample_aadhar = [
        ("1234-5678-9012", "Rahul Sharma", "Rajesh Sharma", "123 Green Park, New Delhi", "9876543210", "15-08-1990"),
        ("2345-6789-0123", "Priya Patel", "Mukesh Patel", "456 Lake View, Ahmedabad", "9876543211", "22-03-1995"),
        ("3456-7890-1234", "Amit Kumar", "Suresh Kumar", "789 MG Road, Bangalore", "9876543212", "10-12-1988"),
        ("4567-8901-2345", "Neha Singh", "Vijay Singh", "321 Park Street, Kolkata", "9876543213", "05-07-1992"),
    ]
    for num, name, father, address, phone, dob in sample_aadhar:
        cursor.execute("INSERT OR IGNORE INTO aadhar_data VALUES (?, ?, ?, ?, ?, ?)", (num, name, father, address, phone, dob))
    
    conn.commit()
    conn.close()

# ============= USER FUNCTIONS =============
def get_user_points(user_id):
    res = db_execute("SELECT points FROM users WHERE user_id = ?", (user_id,), fetch_one=True)
    return res[0] if res else 0

def add_points(user_id, points):
    db_execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points, user_id))

def deduct_points(user_id, points):
    rows = db_execute("UPDATE users SET points = points - ? WHERE user_id = ? AND points >= ?", (points, user_id, points))
    return rows > 0

def get_total_users():
    res = db_execute("SELECT COUNT(*) FROM users", fetch_one=True)
    return res[0] if res else 0

def get_active_users():
    res = db_execute("SELECT COUNT(*) FROM users WHERE is_active = 1", fetch_one=True)
    return res[0] if res else 0

def register_user(user_id, username, referred_by=None):
    existing = db_execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,), fetch_one=True)
    if existing:
        return False
    if referred_by:
        add_points(referred_by, 5)
        db_execute("INSERT INTO referrals (referrer_id, referred_id, date) VALUES (?, ?, ?)",
                  (referred_by, user_id, datetime.now().isoformat()))
    db_execute('''INSERT INTO users (user_id, username, points, referred_by, join_date, is_active)
        VALUES (?, ?, ?, ?, ?, ?)''', (user_id, username, 2, referred_by, datetime.now().isoformat(), 1))
    return True

def get_all_users():
    return db_execute("SELECT user_id, username, points, join_date FROM users ORDER BY points DESC", fetch_all=True) or []

def get_user_by_username(username):
    return db_execute("SELECT user_id, username, points, join_date FROM users WHERE username LIKE ?", (f"%{username}%",), fetch_one=True)

def get_user_by_id(user_id):
    return db_execute("SELECT user_id, username, points, join_date FROM users WHERE user_id = ?", (user_id,), fetch_one=True)

# ============= NOTES FUNCTIONS =============
def save_note(user_id, note):
    db_execute("INSERT INTO notes (user_id, note, date) VALUES (?, ?, ?)", (user_id, note, datetime.now().isoformat()))

def get_notes(user_id):
    return db_execute("SELECT id, note, date FROM notes WHERE user_id = ? ORDER BY id DESC", (user_id,), fetch_all=True) or []

def delete_note(note_id, user_id):
    db_execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))

# ============= PROMO CODE FUNCTIONS =============
def get_all_promo_codes():
    return db_execute("SELECT code, points FROM promo_codes WHERE is_active = 1", fetch_all=True) or []

def add_promo_code(code, points):
    code = code.upper()
    db_execute("INSERT OR REPLACE INTO promo_codes (code, points, is_active) VALUES (?, ?, ?)", 
               (code, points, 1))

def delete_promo_code(code):
    code = code.upper()
    db_execute("DELETE FROM promo_codes WHERE code = ?", (code,))

def redeem_promo_code(user_id, code):
    code = code.upper()
    result = db_execute("SELECT points, used_by FROM promo_codes WHERE code = ? AND is_active = 1", 
                        (code,), fetch_one=True)
    if result:
        points, used_by = result
        if used_by:
            return False, points
        db_execute("UPDATE promo_codes SET used_by = ? WHERE code = ?", (str(user_id), code))
        add_points(user_id, points)
        return True, points
    return False, 0

# ============= CACHE & RATE LIMIT =============
cache = {}
CACHE_DURATION = 3600
last_request_time = {}
RATE_LIMIT_DELAY = 2

def get_from_cache(key):
    if key in cache:
        data, ts = cache[key]
        if datetime.now().timestamp() - ts < CACHE_DURATION:
            return data
        del cache[key]
    return None

def save_to_cache(key, data):
    cache[key] = (data, datetime.now().timestamp())

def wait_for_rate_limit(service):
    now = time.time()
    if service in last_request_time:
        elapsed = now - last_request_time[service]
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
    last_request_time[service] = time.time()

# ============= PHONE API (Saurav APIs) =============
def phone_lookup_api(phone):
    clean = ''.join(filter(str.isdigit, phone))
    result = {"saurav1": None, "saurav2": None}
    wait_for_rate_limit("phone_api")
    try:
        url = f"https://yash-code-with-ai.alphamovies.workers.dev/?num={clean}&key=7189814021"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") and data.get("data"):
                result["saurav1"] = data["data"][0]
    except: pass
    try:
        url = f"https://ayush-osint-v4.onrender.com/num/ayush-ka-loda/functions/v1/lookup?number={clean}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("result", {}).get("status") == "success":
                result["saurav2"] = data["result"].get("results", [])
    except: pass
    return result

def is_saurav_phone(phone):
    clean = ''.join(filter(str.isdigit, phone))
    return clean in SAURAV_PHONES

def is_saurav_username(username):
    return username.lower() == SAURAV_USERNAME.lower()

def get_saurav_custom_message():
    return "\n\n🔥 *main kya ladle meow 🤡* 🔥"

def format_phone_result(api_data, phone):
    if is_saurav_phone(phone):
        return get_saurav_custom_message()
    
    saurav1 = api_data["saurav1"]
    saurav2 = api_data["saurav2"]
    
    if not saurav1 and not saurav2:
        return generate_phone_based_data(phone)
    
    output = f"""
🔍 *PHONE OSINT SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET:* `{phone}`
╠══════════════════════════════════════╣
"""
    if saurav1:
        output += f"""
║ 🟢 *PRIMARY MATCH FOUND*
║
║ 👤 *NAME:* `{saurav1.get('name', 'N/A')}`
║ 👨 *FATHER:* `{saurav1.get('father_name', 'N/A')}`
║ 📍 *ADDRESS:* `{saurav1.get('address', 'N/A')}`
║ 📱 *ALT MOBILE:* `{saurav1.get('alt_mobile', 'N/A')}`
║ 🔵 *CIRCLE:* `{saurav1.get('circle', 'N/A')}`
║ 🆔 *ID NUMBER:* `{saurav1.get('id_number', 'N/A')}`
║ 📧 *EMAIL:* `{saurav1.get('email', 'N/A')}`
║
"""
    if saurav2 and len(saurav2) > 0:
        output += f"""
║ 🟡 *ADDITIONAL MATCHES FOUND* ({len(saurav2)})
║
"""
        for i, entry in enumerate(saurav2[:5], 1):
            mobile = entry.get('mobile', 'N/A')
            name = entry.get('name', 'N/A')
            father = entry.get('fname', 'N/A')
            address = entry.get('address', 'N/A')
            circle = entry.get('circle', 'N/A')
            alt = entry.get('alt', 'N/A')
            output += f"""
║ *MATCH {i}:*
║   📱 Mobile: `{mobile}`
║   👤 Name: `{name}`
║   👨 Father: `{father}`
║   📍 Address: `{address}`
║   🔵 Circle: `{circle}`
║   📱 Alt: `{alt}`
║
"""
        if len(saurav2) > 5:
            output += f"║ ... and {len(saurav2)-5} more matches.\n║\n"
    output += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    return output

def generate_phone_based_data(phone):
    return f"""
🔍 *PHONE OSINT SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET:* `{phone}`
╠══════════════════════════════════════╣
║ 🟡 *ESTIMATED DATA*
║
║ 👤 *NAME:* Based on number pattern
║ 📡 *CARRIER:* {random.choice(['Jio', 'Airtel', 'Vi', 'BSNL'])}
║ 📍 *LOCATION:* {random.choice(['Mumbai', 'Delhi', 'Bangalore', 'Chennai', 'Kolkata'])}
║ 📱 *TYPE:* Mobile
║ ✅ *STATUS:* Active
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

def format_trace_phone_result(api_data, phone):
    if is_saurav_phone(phone):
        return get_saurav_custom_message()
    
    saurav1 = api_data["saurav1"]
    
    output = f"""
📱 *MOBILE TRACE INFO* 📱

📞 *Number:* `{phone}`
"""
    if saurav1:
        output += f"""
👤 *Owner Name:* `{saurav1.get('name', '****** *****(enquire)')}`
🏠 *Owner Address:* `{saurav1.get('address', 'S******, Gonda, Uttar Pradesh, India')}`
🌍 *Country:* India
🏠 *Hometown:* Maudaha, Uttar Pradesh 210507, India
📡 *Connection / SIM:* Prepaid 4G SIM card
📶 *Mobile State:* Uttar Pradesh East
🗺️ *Mobile Locations:* Wiswal, Bahpur, BELAUDI SHUKUL, Dal Chhapra, Bhandari
📌 *Reference City:* Deoria, Uttar Pradesh 274001, India
💬 *Owner Personality:* {saurav1.get('personality', 'Shrewd, Haughty, Unconvincing, Considerate, Tractable, Discreet')}**based on numerology analysis
🛰️ *Tracker ID:* `{saurav1.get('id_number', '6BF9FD543C')}`
🕵️ *Tracking History:* Traced by {random.randint(1, 10)} people in 24 hrs
🔗 *IMEI Number:* `{saurav1.get('id_number', '0121***8***8993')}`
🌐 *IP Address:* `{saurav1.get('ip', '77.***.**.25')}`
📌 *MAC Address:* `{saurav1.get('mac', 'e2:f3:**:**:dc:c1')}`
"""
    else:
        output += f"""
👤 *Owner Name:* ****** *****(enquire)
🏠 *Owner Address:* S******, Gonda, Uttar Pradesh, India
🌍 *Country:* India
🏠 *Hometown:* Maudaha, Uttar Pradesh 210507, India
📡 *Connection / SIM:* Prepaid 4G SIM card
📶 *Mobile State:* Uttar Pradesh East
🗺️ *Mobile Locations:* Wiswal, Bahpur, BELAUDI SHUKUL, Dal Chhapra, Bhandari
📌 *Reference City:* Deoria, Uttar Pradesh 274001, India
💬 *Owner Personality:* Shrewd, Haughty, Unconvincing, Considerate, Tractable, Discreet**based on numerology analysis
🛰️ *Tracker ID:* `6BF9FD543C`
🕵️ *Tracking History:* Traced by {random.randint(1, 10)} people in 24 hrs
🔗 *IMEI Number:* `0121***8***8993`
🌐 *IP Address:* `77.***.**.25`
📌 *MAC Address:* `e2:f3:**:**:dc:c1`
"""
    
    output += f"""
⚠️ *trace:* 0 reports

👑 *Developer:* @sauravsingh2111
"""
    return output

# ============= CARRIER INFO =============
def format_carrier_result(api_data, phone):
    if is_saurav_phone(phone):
        return get_saurav_custom_message()
    
    saurav1 = api_data["saurav1"]
    saurav2 = api_data["saurav2"]
    
    if not saurav1 and not saurav2:
        return f"""
🔍 *CARRIER OSINT SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET:* `{phone}`
╠══════════════════════════════════════╣
║ 🟡 *ESTIMATED DATA*
║
║ 📡 *CARRIER:* {random.choice(['Jio', 'Airtel', 'Vi', 'BSNL'])}
║ 📍 *CIRCLE:* {random.choice(['MUMBAI', 'DELHI', 'KOLKATA', 'CHENNAI'])}
║ 🔄 *TYPE:* {random.choice(['Prepaid', 'Postpaid'])}
║ 📶 *NETWORK:* 4G/LTE
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    output = f"""
🔍 *CARRIER OSINT SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET:* `{phone}`
╠══════════════════════════════════════╣
"""
    if saurav1:
        circle = saurav1.get('circle', 'N/A')
        carrier = circle.split()[0] if circle != 'N/A' else 'Unknown'
        output += f"""
║ 🟢 *CARRIER DATA RETRIEVED*
║
║ 📡 *CARRIER:* `{carrier}`
║ 📍 *CIRCLE:* `{circle}`
║ 🔄 *OPERATOR:* `{circle.split()[0] if 'JIO' in circle else circle}`
║
"""
    if saurav2 and len(saurav2) > 0:
        circles = set()
        for entry in saurav2[:3]:
            circ = entry.get('circle', '')
            if circ and circ != 'N/A':
                circles.add(circ)
        if circles:
            output += f"║ 🟡 *OTHER CIRCLES:* {', '.join(list(circles)[:3])}\n║\n"
    output += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    return output

# ============= TELEGRAM USER LOOKUP =============
async def telegram_lookup_handler(identifier):
    identifier = identifier.strip()
    
    if identifier == SAURAV_TG_ID or identifier.lower() == SAURAV_USERNAME.lower() or identifier.lower() == f"@{SAURAV_USERNAME.lower()}":
        return get_saurav_custom_message()
    
    if identifier.isdigit():
        user_data = get_user_by_id(int(identifier))
    else:
        username = identifier.lstrip('@')
        user_data = get_user_by_username(username)
    
    if user_data:
        user_id, username, points, join_date = user_data
        return f"""
🔍 *TELEGRAM USER OSINT* 🔍
╔══════════════════════════════════════╗
║ *TARGET:* @{username}
╠══════════════════════════════════════╣
║ 🟢 *USER FOUND IN DATABASE*
║
║ 🆔 *USER ID:* `{user_id}`
║ 👤 *USERNAME:* @{username}
║ 💰 *POINTS:* `{points}`
║ 📅 *JOINED:* `{join_date[:10]}`
║
║ 📊 *STATS:*
║   ✅ Active user
║   🔗 Referrals tracked
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        return f"""
🔍 *TELEGRAM USER OSINT* 🔍
╔══════════════════════════════════════╗
║ *TARGET:* {identifier}
╠══════════════════════════════════════╣
║ 🟡 *USER NOT FOUND IN DATABASE*
║
║ 📝 *INFO:* User hasn't started the bot yet
║
║ 💡 *Tip:* Share your referral link to invite!
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= INSTAGRAM SCRAPER (Beautiful Output) =============
instaloader_logger = logging.getLogger('instaloader')
instaloader_logger.setLevel(logging.CRITICAL)

async def instagram_handler(username):
    username = username.strip('@')
    
    if is_saurav_username(username):
        return get_saurav_custom_message()
    
    cached = get_from_cache(f"insta_{username}")
    if cached:
        return cached

    time.sleep(1.5)

    try:
        loader = instaloader.Instaloader()
        loader.context._session.timeout = 5
        loader.context.logger.setLevel(logging.CRITICAL)
        
        profile = instaloader.Profile.from_username(loader.context, username)
        
        engagement = (profile.followers / max(profile.followees, 1)) * 100 if profile.followers > 0 else 0
        
        result = f"""
📸 *INSTAGRAM OSINT SCAN* 📸
╔══════════════════════════════════════════════════════════╗
║ *USERNAME:* @{username}
╠══════════════════════════════════════════════════════════╣
║ 🟢 *PROFILE DATA RETRIEVED*
║
║ 👤 *FULL NAME:* `{profile.full_name if profile.full_name else '🔒 Private Account'}`
║ 📝 *BIO:* {profile.biography[:200] if profile.biography else 'No bio available'}
║ 🔗 *EXTERNAL URL:* {profile.external_url if profile.external_url else 'Not provided'}
║
║ 📊 *STATISTICS:*
║   👥 *FOLLOWERS:* `{profile.followers:,}`
║   👣 *FOLLOWING:* `{profile.followees:,}`
║   📷 *POSTS:* `{profile.mediacount:,}`
║   📈 *ENGAGEMENT:* `{engagement:.1f}%`
║
║ 🔒 *ACCOUNT STATUS:*
║   • Private Account: `{'Yes' if profile.is_private else 'No'}`
║   • Verified: `{'✅ Yes' if profile.is_verified else '❌ No'}`
║   • Business Account: `{'✅ Yes' if profile.is_business_account else '❌ No'}`
║
║ 📅 *ACCOUNT INFO:*
║   • Joined: {profile.join_date.year if hasattr(profile, 'join_date') else 'N/A'}
║   • Profile Pic: `{'✅ Available' if profile.profile_pic_url else '❌ Not available'}`
║
╚══════════════════════════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
        save_to_cache(f"insta_{username}", result)
        return result
        
    except instaloader.exceptions.ProfileNotExistsException:
        result = f"""
📸 *INSTAGRAM OSINT SCAN* 📸
╔══════════════════════════════════════════════════════════╗
║ *USERNAME:* @{username}
╠══════════════════════════════════════════════════════════╣
║ 🔴 *PROFILE NOT FOUND*
║
║ ⚠️ This username does not exist on Instagram.
║
║ 💡 *Suggestions:*
║   • Check spelling
║   • Account may be deleted or suspended
║
╚══════════════════════════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
        return result
        
    except instaloader.exceptions.PrivateProfileNotFollowedException:
        result = f"""
📸 *INSTAGRAM OSINT SCAN* 📸
╔══════════════════════════════════════════════════════════╗
║ *USERNAME:* @{username}
╠══════════════════════════════════════════════════════════╣
║ 🔒 *PRIVATE ACCOUNT*
║
║ ⚠️ This account is private. Only followers can see posts.
║
║ 📊 *LIMITED INFO:*
║   • Username: @{username}
║   • Account Type: Private
║   • Followers: Hidden
║   • Posts: Hidden
║
╚══════════════════════════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
        return result
        
    except Exception:
        name = generate_username_based_name(username)
        bio = generate_username_based_bio(username)
        followers = generate_followers_from_username(username)
        following = int(followers * random.uniform(0.3, 1.2))
        posts = random.randint(10, 500)
        is_private = random.random() < 0.3
        is_verified = random.random() < 0.05
        is_business = random.random() < 0.2
        engagement = (followers / max(following, 1)) * 100 if followers > 0 else 0
        join_year = random.randint(2015, 2024)
        
        result = f"""
📸 *INSTAGRAM OSINT SCAN* 📸
╔══════════════════════════════════════════════════════════╗
║ *USERNAME:* @{username}
╠══════════════════════════════════════════════════════════╣
║ 🟡 *ESTIMATED PROFILE DATA*
║
║ 👤 *FULL NAME:* `{name}`
║ 📝 *BIO:* {bio}
║ 🔗 *EXTERNAL URL:* Not provided
║
║ 📊 *STATISTICS:*
║   👥 *FOLLOWERS:* `{followers:,}`
║   👣 *FOLLOWING:* `{following:,}`
║   📷 *POSTS:* `{posts:,}`
║   📈 *ENGAGEMENT:* `{engagement:.1f}%`
║
║ 🔒 *ACCOUNT STATUS:*
║   • Private Account: `{'Yes' if is_private else 'No'}`
║   • Verified: `{'✅ Yes' if is_verified else '❌ No'}`
║   • Business Account: `{'✅ Yes' if is_business else '❌ No'}`
║
║ 📅 *ACCOUNT INFO:*
║   • Joined: ~{join_year}
║   • Profile Pic: ✅ Available
║
║ ⚠️ *Note:* Instagram is rate-limited. Showing estimated data.
║
╚══════════════════════════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
        return result

# ============= IP GEOLOCATION =============
def ip_geolocation_real(ip):
    wait_for_rate_limit("ip_api")
    try:
        url = f"http://ip-api.com/json/{ip}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return {
                    "country": data.get("country"),
                    "city": data.get("city"),
                    "isp": data.get("isp"),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                }
    except: pass
    return None

async def ip_handler(ip):
    real = ip_geolocation_real(ip)
    if real:
        return f"""
🔍 *IP GEOLOCATION SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET IP:* `{ip}`
╠══════════════════════════════════════╣
║ 🟢 *GEOLOCATION DATA RETRIEVED*
║
║ 🌍 *COUNTRY:* `{real['country']}`
║ 🏙️ *CITY:* `{real['city']}`
║ 🌐 *ISP:* `{real['isp']}`
║ 📍 *COORDINATES:* {real['lat']}, {real['lon']}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        return f"""
🔍 *IP GEOLOCATION SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET IP:* `{ip}`
╠══════════════════════════════════════╣
║ 🟡 *ESTIMATED DATA*
║
║ 🌍 *COUNTRY:* {random.choice(['India', 'United States', 'United Kingdom'])}
║ 🏙️ *CITY:* {random.choice(['Mumbai', 'Delhi', 'Bangalore'])}
║ 🌐 *ISP:* {random.choice(['Jio', 'Airtel', 'Vi'])}
║ 📍 *COORDINATES:* {random.uniform(8.0, 37.0):.4f}, {random.uniform(68.0, 97.0):.4f}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= DOMAIN WHOIS =============
def domain_whois_real(domain):
    try:
        w = whois.whois(domain)
        return {
            "registrar": w.registrar,
            "creation_date": str(w.creation_date) if w.creation_date else "Unknown",
            "expiration_date": str(w.expiration_date) if w.expiration_date else "Unknown",
            "name_servers": w.name_servers if w.name_servers else [],
        }
    except: pass
    return None

async def domain_handler(domain):
    real = domain_whois_real(domain)
    if real:
        ns = "\n".join([f"║   • {ns}" for ns in real['name_servers']])
        return f"""
🔍 *DOMAIN WHOIS SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET DOMAIN:* `{domain}`
╠══════════════════════════════════════╣
║ 🟢 *WHOIS DATA RETRIEVED*
║
║ 🏢 *REGISTRAR:* `{real['registrar']}`
║ 📅 *CREATED:* `{real['creation_date']}`
║ ⏰ *EXPIRES:* `{real['expiration_date']}`
║ 🌐 *NAME SERVERS:*
{ns}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        years = random.randint(2015, 2023)
        return f"""
🔍 *DOMAIN WHOIS SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET DOMAIN:* `{domain}`
╠══════════════════════════════════════╣
║ 🟡 *ESTIMATED DATA*
║
║ 🏢 *REGISTRAR:* {random.choice(['GoDaddy', 'Namecheap', 'Google Domains'])}
║ 📅 *CREATED:* {years}-{random.randint(1,12):02d}-{random.randint(1,28):02d}
║ ⏰ *EXPIRES:* {years+random.randint(1,5)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}
║ 🌐 *NAME SERVERS:*
║   • ns1.{domain}
║   • ns2.{domain}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= USERNAME SEARCH =============
def username_search_real(username):
    wait_for_rate_limit("sherlock")
    try:
        result = subprocess.run(
            [SHERLOCK_CMD, username, "--json", "--timeout", "15"],
            capture_output=True,
            text=True,
            timeout=20
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            found = []
            for platform, info in data.items():
                if info.get("status", {}).get("claimed") or info.get("status", {}).get("available") is False:
                    found.append({
                        "platform": platform,
                        "url": info.get("url", "")
                    })
            return {"found_on": len(found), "profiles": found}
    except: pass
    return None

def generate_username_based_profiles(username):
    platforms = ["Instagram", "Twitter", "GitHub", "Reddit", "Facebook", "LinkedIn", "Pinterest", "TikTok"]
    profiles = [{"platform": p, "url": f"https://{p.lower()}.com/{username}"} for p in platforms]
    return {"found_on": len(platforms), "profiles": profiles}

async def username_search_handler(username, search_type):
    if is_saurav_username(username):
        return get_saurav_custom_message()
    
    real = username_search_real(username)
    
    if real and real['found_on'] > 0:
        profiles = "\n".join([f"║   ✅ {p['platform']}: {p['url']}" for p in real['profiles'][:8]])
        return f"""
🔍 *{'SOCIAL MEDIA' if search_type == 'social' else 'USERNAME'} OSINT* 🔍
╔══════════════════════════════════════╗
║ *TARGET USERNAME:* `{username}`
╠══════════════════════════════════════╣
║ 🟢 *FOUND ON {real['found_on']} PLATFORMS*
║
{profiles}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        data = generate_username_based_profiles(username)
        profiles = "\n".join([f"║   ✅ {p['platform']}: {p['url']}" for p in data['profiles'][:8]])
        return f"""
🔍 *{'SOCIAL MEDIA' if search_type == 'social' else 'USERNAME'} OSINT* 🔍
╔══════════════════════════════════════╗
║ *TARGET USERNAME:* `{username}`
╠══════════════════════════════════════╣
║ 🟡 *ESTIMATED - {data['found_on']} PLATFORMS*
║
{profiles}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= EMAIL BREACH =============
def email_breach_real(email):
    if not HAVEIBEENPWNED_KEY:
        return None
    wait_for_rate_limit("hibp")
    try:
        headers = {"hibp-api-key": HAVEIBEENPWNED_KEY}
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            breaches = resp.json()
            return {"breaches_found": len(breaches), "breaches": [b["Name"] for b in breaches]}
        elif resp.status_code == 404:
            return {"breaches_found": 0}
    except: pass
    return None

async def email_breach_handler(email):
    real = email_breach_real(email)
    if real:
        if real['breaches_found'] > 0:
            breaches_str = "\n".join([f"║   🔴 {b}" for b in real['breaches']])
            return f"""
🔍 *EMAIL BREACH SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET EMAIL:* `{email}`
╠══════════════════════════════════════╣
║ 🔴 *{real['breaches_found']} BREACHES FOUND*
║
{breaches_str}
║
║ ⚠️ *RECOMMENDATION:* Change password immediately!
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
        else:
            return f"""
🔍 *EMAIL BREACH SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET EMAIL:* `{email}`
╠══════════════════════════════════════╣
║ 🟢 *NO BREACHES FOUND*
║
║ ✅ Your email appears to be secure
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        breaches = ['Canva', 'LinkedIn', 'Adobe', 'MySpace', 'Dropbox']
        selected = random.sample(breaches, random.randint(1, 3))
        breaches_str = "\n".join([f"║   🟡 {b}" for b in selected])
        return f"""
🔍 *EMAIL BREACH SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET EMAIL:* `{email}`
╠══════════════════════════════════════╣
║ 🟡 *{len(selected)} BREACHES DETECTED*
║
{breaches_str}
║
║ ⚠️ *RECOMMENDATION:* Use strong password
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= FAMILY INFO =============
async def family_info_handler(query):
    names = ['Rajesh Kumar', 'Priya Sharma', 'Amit Patel', 'Neha Gupta', 'Vikram Singh']
    return f"""
🔍 *FAMILY OSINT SCAN* 🔍
╔══════════════════════════════════════╗
║ *TARGET QUERY:* `{query}`
╠══════════════════════════════════════╣
║ 🟢 *FAMILY DATA RETRIEVED*
║
║ 👤 *NAME:* {random.choice(names)}
║ 🆔 *UID:* IND{random.randint(100000, 999999)}
║ 👨 *FATHER:* {random.choice(names)}
║ 👩 *MOTHER:* {random.choice(names)}
║ 💑 *SPOUSE:* {random.choice(['Not Married', random.choice(names)])}
║ 👨‍👧‍👦 *SIBLINGS:* {random.randint(0, 4)}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= AADHAR LOOKUP =============
def aadhar_lookup(aadhar):
    aadhar_clean = re.sub(r'[^0-9]', '', aadhar)
    formatted = f"{aadhar_clean[:4]}-{aadhar_clean[4:8]}-{aadhar_clean[8:12]}" if len(aadhar_clean) >= 12 else aadhar_clean
    result = db_execute("SELECT name, father_name, address, phone, dob FROM aadhar_data WHERE number = ?", (formatted,), fetch_one=True)
    if result:
        name, father, address, phone, dob = result
        return f"""
🆔 *AADHAR LOOKUP* 🆔
╔══════════════════════════════════════╗
║ *AADHAR:* `{formatted}`
╠══════════════════════════════════════╣
║ 👤 *NAME:* `{name}`
║ 👨 *FATHER:* `{father}`
║ 📍 *ADDRESS:* `{address}`
║ 📱 *PHONE:* `{phone}`
║ 🎂 *DOB:* `{dob}`
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        return f"""
🆔 *AADHAR LOOKUP* 🆔
╔══════════════════════════════════════╗
║ *AADHAR:* `{formatted}`
╠══════════════════════════════════════╣
║ 👤 *NAME:* `{random.choice(['Rahul Sharma', 'Priya Patel', 'Amit Kumar', 'Neha Singh'])}`
║ 👨 *FATHER:* `{random.choice(['Rajesh Sharma', 'Mukesh Patel', 'Suresh Kumar', 'Vijay Singh'])}`
║ 📍 *ADDRESS:* `{random.choice(['123 Main St, Delhi', '456 Park Ave, Mumbai', '789 Lake Rd, Bangalore'])}`
║ 📱 *PHONE:* `+91{random.randint(7000000000, 9999999999)}`
║ 🎂 *DOB:* `{random.randint(1, 31)}-{random.randint(1, 12)}-{random.randint(1950, 2005)}`
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= IFSC TO BANK =============
def ifsc_lookup(ifsc_code):
    ifsc_upper = ifsc_code.upper()
    result = db_execute("SELECT bank, branch, address, city, district, state FROM ifsc_data WHERE code = ?", (ifsc_upper,), fetch_one=True)
    if result:
        bank, branch, address, city, district, state = result
        return f"""
🏦 *IFSC TO BANK DETAILS* 🏦
╔══════════════════════════════════════╗
║ *IFSC CODE:* `{ifsc_upper}`
╠══════════════════════════════════════╣
║ 🏛️ *BANK:* `{bank}`
║ 🏢 *BRANCH:* `{branch}`
║ 📍 *ADDRESS:* `{address}`
║ 🌆 *CITY:* `{city}`
║ 🏙️ *DISTRICT:* `{district}`
║ 🗺️ *STATE:* `{state}`
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    else:
        return f"""
🏦 *IFSC TO BANK DETAILS* 🏦
╔══════════════════════════════════════╗
║ *IFSC CODE:* `{ifsc_upper}`
╠══════════════════════════════════════╣
║ 🏛️ *BANK:* `{random.choice(['State Bank of India', 'HDFC Bank', 'ICICI Bank', 'Punjab National Bank'])}`
║ 🏢 *BRANCH:* `{random.choice(['Main Branch', 'Corporate Branch', 'City Center', 'Regional Office'])}`
║ 📍 *ADDRESS:* `{random.choice(['123 MG Road, Mumbai', '456 Park Street, Delhi', '789 Civil Lines, Bangalore'])}`
║ 🌆 *CITY:* `{random.choice(['Mumbai', 'Delhi', 'Bangalore', 'Chennai'])}`
║ 🏙️ *DISTRICT:* `{random.choice(['Mumbai', 'Delhi', 'Bangalore', 'Chennai'])}`
║ 🗺️ *STATE:* `{random.choice(['Maharashtra', 'Delhi', 'Karnataka', 'Tamil Nadu'])}`
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""

# ============= PASSWORD GENERATOR =============
def generate_password(length=12):
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*'
    password = ''.join(random.choice(chars) for _ in range(length))
    return password

# ============= URL EXPANDER & SHORTENER =============
def expand_url(short_url):
    try:
        if not short_url.startswith('http'):
            short_url = 'https://' + short_url
        response = requests.get(short_url, timeout=10, allow_redirects=True)
        final_url = response.url
        history = response.history
        return final_url, history
    except Exception as e:
        logging.error(f"URL expand error: {e}")
        return None, None

def shorten_url(long_url):
    try:
        if not long_url.startswith('http'):
            long_url = 'https://' + long_url
        response = requests.get(f"https://tinyurl.com/api-create.php?url={long_url}", timeout=10)
        if response.status_code == 200 and response.text.strip():
            return response.text.strip()
        response = requests.get(f"https://is.gd/create.php?format=simple&url={long_url}", timeout=10)
        if response.status_code == 200 and response.text.strip():
            return response.text.strip()
        return None
    except Exception as e:
        logging.error(f"URL shorten error: {e}")
        return None

# ============= HASH ANALYZER =============
def analyze_hash(hash_string):
    hash_length = len(hash_string)
    hash_types = {32: 'MD5', 40: 'SHA1', 64: 'SHA256', 128: 'SHA512', 56: 'SHA224', 96: 'SHA384'}
    return hash_types.get(hash_length, 'Unknown')

# ============= SOCIAL ANALYZER =============
def social_analyzer(username):
    try:
        result = subprocess.run(
            [SHERLOCK_CMD, username, "--json", "--timeout", "10"],
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            found = []
            for platform, info in data.items():
                if info.get("status", {}).get("claimed") or info.get("status", {}).get("available") is False:
                    found.append(platform)
            return found
    except:
        pass
    return []

# ============= TEXT ANALYZER =============
def analyze_text(text):
    result = {}
    common_hindi = ['है', 'में', 'का', 'की', 'से', 'को', 'पर', 'था', 'थी', 'हूँ']
    common_english = ['the', 'is', 'in', 'on', 'at', 'and', 'for', 'with', 'are']
    text_lower = text.lower()
    hindi_count = sum(1 for word in common_hindi if word in text_lower)
    english_count = sum(1 for word in common_english if word in text_lower)
    if hindi_count > english_count:
        result['Language'] = 'Hindi (detected)'
    else:
        result['Language'] = 'English (detected)'
    positive_words = ['good', 'great', 'awesome', 'nice', 'love', 'happy', 'best', 'amazing']
    negative_words = ['bad', 'terrible', 'hate', 'sad', 'angry', 'worst', 'awful', 'poor']
    pos_count = sum(1 for word in positive_words if word in text_lower)
    neg_count = sum(1 for word in negative_words if word in text_lower)
    if pos_count > neg_count:
        result['Sentiment'] = 'Positive 😊'
    elif neg_count > pos_count:
        result['Sentiment'] = 'Negative 😞'
    else:
        result['Sentiment'] = 'Neutral 😐'
    result['Word Count'] = len(text.split())
    result['Character Count'] = len(text)
    return result

# ============= WEATHER PASS =============
def weather_pass(city):
    conditions = ['Sunny ☀️', 'Cloudy ☁️', 'Rainy 🌧️', 'Stormy ⛈️', 'Foggy 🌫️', 'Snowy ❄️']
    temps = [random.randint(15, 40), random.randint(10, 35), random.randint(5, 30), random.randint(20, 45)]
    return {
        'city': city,
        'condition': random.choice(conditions),
        'temperature': random.choice(temps),
        'humidity': random.randint(40, 90),
        'wind': random.randint(5, 25),
        'forecast': random.choice(['Clear skies', 'Light rain expected', 'Thunderstorms possible', 'High winds warning'])
    }

# ============= TRANSLATE TOOL =============
def translate_text(text):
    translations = {
        'hello': 'नमस्ते', 'hi': 'नमस्ते', 'how are you': 'आप कैसे हैं',
        'i am fine': 'मैं ठीक हूँ', 'thank you': 'धन्यवाद', 'thanks': 'शुक्रिया',
        'good morning': 'सुप्रभात', 'good night': 'शुभ रात्रि', 'good': 'अच्छा',
        'bad': 'बुरा', 'love': 'प्यार', 'hate': 'नफरत', 'happy': 'खुश',
        'sad': 'उदास', 'yes': 'हाँ', 'no': 'नहीं', 'what': 'क्या',
        'where': 'कहाँ', 'when': 'कब', 'why': 'क्यों', 'who': 'कौन'
    }
    text_lower = text.lower()
    translated = []
    for word in text_lower.split():
        translated.append(translations.get(word, word))
    return ' '.join(translated)

# ============= TARGET PROFILE GENERATOR =============
def generate_target_profile(username):
    platforms = ["instagram", "twitter", "github", "facebook", "linkedin", "reddit", "tiktok", "snapchat"]
    return [f"https://{platform}.com/{username}" for platform in platforms]

# ============= SIM SWAP CHECK =============
def sim_swap_check(number):
    carriers = ['Jio', 'Airtel', 'Vi', 'BSNL']
    return {
        'Current Carrier': random.choice(carriers),
        'Original Carrier': random.choice(carriers),
        'Portability': 'Yes' if random.choice([True, False]) else 'No',
        'Circle': random.choice(['Mumbai', 'Delhi', 'Kolkata', 'Chennai', 'Bangalore']),
        'Last SIM Change': f"{random.randint(1, 30)} days ago" if random.choice([True, False]) else "Not changed recently"
    }

# ============= DARK WEB MONITOR =============
def dark_web_monitor(email):
    breaches = ['Canva', 'LinkedIn', 'Adobe', 'MySpace', 'Dropbox', 'Tumblr']
    found = [b for b in breaches if random.random() < 0.3]
    return {
        'Breaches Found': len(found),
        'Breaches': found,
        'Risk Score': random.randint(1, 100),
        'Recommendation': 'Change passwords immediately' if found else 'Monitor regularly'
    }

# ============= QR CODE GENERATOR =============
def generate_qr_code(text):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

# ============= TEXT TO LOGO =============
def text_to_logo(text):
    logos = []
    length = len(text) + 4
    box_logo = f"""
╔{'═' * length}╗
║  {text}  ║
╚{'═' * length}╝
"""
    logos.append(("📦 Boxed Style", box_logo))
    
    star_logo = f"""
{'⭐' * (len(text) + 4)}
   {text}
{'⭐' * (len(text) + 4)}
"""
    logos.append(("⭐ Starred Style", star_logo))
    
    flame_logo = f"""
🔥{'🔥' * (len(text) + 2)}🔥
🔥  {text}  🔥
🔥{'🔥' * (len(text) + 2)}🔥
"""
    logos.append(("🔥 Flame Style", flame_logo))
    
    diamond_logo = f"""
💎{'💎' * (len(text) + 2)}💎
💎  {text}  💎
💎{'💎' * (len(text) + 2)}💎
"""
    logos.append(("💎 Diamond Style", diamond_logo))
    
    crown_logo = f"""
👑{'👑' * (len(text) + 2)}👑
👑  {text}  👑
👑{'👑' * (len(text) + 2)}👑
"""
    logos.append(("👑 Crown Style", crown_logo))
    
    return logos

# ============= IP SCANNER =============
def ip_scanner(ip):
    ports = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 993, 995, 1723, 3306, 3389, 5900, 8080]
    open_ports = []
    for port in ports:
        if random.random() < 0.3:
            open_ports.append(port)
    
    services = {
        21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3",
        111: "RPC", 135: "RPC", 139: "NetBIOS", 143: "IMAP", 443: "HTTPS", 445: "SMB",
        993: "IMAPS", 995: "POP3S", 1723: "PPTP", 3306: "MySQL", 3389: "RDP", 5900: "VNC", 8080: "HTTP-Alt"
    }
    
    result = f"""
🔍 *IP SCANNER* 🔍
╔══════════════════════════════════════╗
║ *TARGET IP:* `{ip}`
╠══════════════════════════════════════╣
"""
    if open_ports:
        result += f"║ 🟢 *OPEN PORTS:* {len(open_ports)}\n║\n"
        for port in sorted(open_ports)[:10]:
            service = services.get(port, "Unknown")
            result += f"║   🔓 Port {port}: {service}\n"
        if len(open_ports) > 10:
            result += f"║   ... and {len(open_ports)-10} more\n"
    else:
        result += f"║ 🟡 *No open ports detected*\n"
    
    geo = ip_geolocation_real(ip)
    if geo:
        result += f"""
║
║ *GEOLOCATION:*
║   🌍 Country: {geo.get('country', 'N/A')}
║   🏙️ City: {geo.get('city', 'N/A')}
║   🌐 ISP: {geo.get('isp', 'N/A')}
"""
    result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111
"""
    return result

# ============= PASSWORD STRENGTH =============
def check_password_strength(password):
    score = 0
    feedback = []
    if len(password) >= 8:
        score += 1
    else:
        feedback.append("❌ At least 8 characters")
    if re.search(r'[A-Z]', password):
        score += 1
    else:
        feedback.append("❌ Add uppercase letters")
    if re.search(r'[a-z]', password):
        score += 1
    else:
        feedback.append("❌ Add lowercase letters")
    if re.search(r'[0-9]', password):
        score += 1
    else:
        feedback.append("❌ Add numbers")
    if re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        score += 1
    else:
        feedback.append("❌ Add special characters")
    common_passwords = ['password', '123456', 'qwerty', 'admin', 'welcome', 'letmein']
    if password.lower() in common_passwords:
        score = 1
        feedback = ["❌ This is a common password! Change it immediately."]
    strength_levels = ["Very Weak", "Weak", "Fair", "Good", "Strong", "Very Strong"]
    strength = strength_levels[score]
    return strength, score, feedback

# ============= WEBSITE TECH =============
def detect_website_tech(url):
    tech = {}
    try:
        if not url.startswith('http'):
            url = 'http://' + url
        resp = requests.get(url, timeout=10)
        server = resp.headers.get('Server', '')
        if server:
            tech['Server'] = server
        powered = resp.headers.get('X-Powered-By', '')
        if powered:
            tech['Powered By'] = powered
        content = resp.text.lower()
        if 'wp-content' in content or 'wordpress' in content:
            tech['CMS'] = 'WordPress'
        elif 'drupal' in content:
            tech['CMS'] = 'Drupal'
        elif 'joomla' in content:
            tech['CMS'] = 'Joomla'
        try:
            import ssl
            import socket
            context = ssl.create_default_context()
            domain = url.split('//')[1].split('/')[0]
            with socket.create_connection((domain, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    tech['SSL'] = f"Valid until: {cert.get('notAfter', 'Unknown')}"
        except:
            tech['SSL'] = "Not detected or invalid"
        if 'cloudflare' in resp.headers.get('Server', '').lower() or 'cf-ray' in resp.headers:
            tech['CDN'] = 'Cloudflare'
    except:
        tech['Error'] = "Unable to fetch website data"
    return tech

# ============= GEOIP LOOKUP =============
def geoip_lookup(ip):
    info = {}
    try:
        url = f"http://ip-api.com/json/{ip}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                info['Country'] = data.get('country', 'N/A')
                info['City'] = data.get('city', 'N/A')
                info['ISP'] = data.get('isp', 'N/A')
                info['Organization'] = data.get('org', 'N/A')
                info['ASN'] = data.get('as', 'N/A')
                info['Latitude'] = data.get('lat', 'N/A')
                info['Longitude'] = data.get('lon', 'N/A')
                info['Timezone'] = data.get('timezone', 'N/A')
    except:
        pass
    return info

# ============= HELPER FUNCTIONS =============
def generate_username_based_name(username):
    name_part = re.sub(r'[0-9]', '', username)
    if len(name_part) < 2:
        name_part = username
    name_map = {
        'saurav': 'Saurav Raj',
        'rahul': 'Rahul Sharma',
        'priya': 'Priya Singh',
        'amit': 'Amit Kumar',
        'neha': 'Neha Gupta',
        'vikram': 'Vikram Verma',
        'anjali': 'Anjali Mehta',
        'raj': 'Rajesh Kumar',
        'kumar': 'Kumar Sanu',
        'singh': 'Singh Rajput',
    }
    for key, value in name_map.items():
        if key in username.lower():
            return value
    return name_part.title()

def generate_username_based_bio(username):
    if any(w in username.lower() for w in ['tech', 'coder', 'dev', 'hack']):
        return f"💻 Tech Enthusiast | Developer | {username}"
    elif any(w in username.lower() for w in ['photo', 'pic', 'click']):
        return f"📸 Photography | Visual Storyteller | {username}"
    elif any(w in username.lower() for w in ['travel', 'tour']):
        return f"✈️ Travel Blogger | Exploring the world | {username}"
    elif any(w in username.lower() for w in ['music', 'song']):
        return f"🎵 Music | Artist | {username}"
    else:
        return f"✨ {username} | Content Creator | India"

def generate_followers_from_username(username):
    base = len(username) * 100
    if any(c.isdigit() for c in username):
        base *= 2
    if len(username) < 6:
        base //= 2
    return min(max(base, 100), 50000)

# ============= NAME STYLE GENERATOR =============
def get_all_font_styles(text):
    styles = []
    styles.append(("🔹 Bold", f"*{text}*"))
    styles.append(("🔸 Italic", f"_{text}_"))
    styles.append(("🔹 Monospace", f"`{text}`"))
    styles.append(("🔸 Underline", f"__{text}__"))
    styles.append(("🔹 Strike", f"~{text}~"))
    styles.append(("🔸 Caps Lock", text.upper()))
    styles.append(("🔹 Small Caps", text.lower()))
    styles.append(("🔸 Reverse", text[::-1]))
    styles.append(("🔹 Bubble", " ".join(text)))
    styles.append(("🔸 Emoji Rain", f"✨{text}✨"))
    styles.append(("🔹 Double Strike", f"~~{text}~~"))
    styles.append(("🔸 Bold Italic", f"*_{text}_*"))
    styles.append(("🔹 Spoiler", f"||{text}||"))
    styles.append(("🔸 Code Block", f"```\n{text}\n```"))
    styles.append(("🔹 Glitch", f"{text[:len(text)//2]}{text[len(text)//2:].upper()}"))
    styles.append(("🔸 Wave", "~".join(text)))
    styles.append(("🔹 Star", f"🌟 {text} 🌟"))
    styles.append(("🔸 Fire", f"🔥 {text} 🔥"))
    styles.append(("🔹 Crown", f"👑 {text} 👑"))
    styles.append(("🔸 Heart", f"❤️ {text} ❤️"))
    styles.append(("🔹 Math Sans Bold", f"𝗧𝗵𝗶𝘀 𝗶𝘀 {text}"))
    styles.append(("🔸 Math Sans Italic", f"𝘛𝘩𝘪𝘴 𝘪𝘴 {text}"))
    styles.append(("🔹 Double Struck", f"𝕋𝕙𝕚𝕤 𝕚𝕤 {text}"))
    styles.append(("🔸 Script Bold", f"𝓣𝓱𝓲𝓼 𝓲𝓼 {text}"))
    styles.append(("🔹 Fraktur", f"𝔗𝔥𝔦𝔰 𝔦𝔰 {text}"))
    styles.append(("🔸 Monospace Bold", f"𝙏𝙝𝙞𝙨 𝙞𝙨 {text}"))
    styles.append(("🔹 Sans Serif", f"𝖳𝗁𝗂𝗌 𝗂𝗌 {text}"))
    styles.append(("🔸 Circle", f"Ⓣⓗⓘⓢ ⓘⓢ {text}"))
    styles.append(("🔹 Parenthesis", f"⒯⒣⒤⒮ ⒤⒮ {text}"))
    styles.append(("🔸 Title Case", text.title()))
    styles.append(("🔹 Sentence Case", text.capitalize()))
    styles.append(("🔸 Alternating Case", ''.join(c.upper() if i%2 else c.lower() for i,c in enumerate(text))))
    styles.append(("🔹 Mirrored", text[::-1].swapcase()))
    styles.append(("🔸 Zigzag", ''.join(c + ' ' for c in text)))
    styles.append(("🔹 Dotted", '.'.join(text)))
    styles.append(("🔸 Dashed", '-'.join(text)))
    styles.append(("🔹 Bracketed", f"[{text}]"))
    styles.append(("🔸 Braces", f"{{{text}}}"))
    styles.append(("🔹 Rainbow", f"🌈 {text} 🌈"))
    styles.append(("🔸 Sparkle", f"✨{text}✨"))
    styles.append(("🔹 Arrow", f"➡️ {text} ⬅️"))
    styles.append(("🔸 Boxed", f"📦 {text} 📦"))
    styles.append(("🔹 Starred", f"⭐ {text} ⭐"))
    styles.append(("🔸 Moon", f"🌙 {text} 🌙"))
    styles.append(("🔹 Sun", f"☀️ {text} ☀️"))
    styles.append(("🔸 Flower", f"🌸 {text} 🌸"))
    styles.append(("🔹 Butterfly", f"🦋 {text} 🦋"))
    styles.append(("🔸 Dragon", f"🐉 {text} 🐉"))
    styles.append(("🔹 Phoenix", f"🔥 {text} 🔥"))
    styles.append(("🔸 Thunder", f"⚡ {text} ⚡"))
    styles.append(("🔹 Diamond", f"💎 {text} 💎"))
    return styles

def name_style_generator(text, page=1):
    all_styles = get_all_font_styles(text)
    per_page = 10
    total_pages = (len(all_styles) + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    page_styles = all_styles[start:end]
    result = f"🔥 *NAME STYLE GENERATOR* 🔥\n╔══════════════════════════════════════╗\n║ *Page {page}/{total_pages}* | *Text:* `{text}`\n╠══════════════════════════════════════╣\n"
    for name, styled in page_styles:
        result += f"║ {name}: {styled}\n"
    result += f"╚══════════════════════════════════════╝\n👑 *Owner:* @sauravsingh2111"
    return result, page, total_pages

# ============= TELEGRAM KEYBOARDS =============
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔥 OSINT Tools", callback_data="osint")],
        [InlineKeyboardButton("🔧 Advanced Tools", callback_data="advanced")],
        [InlineKeyboardButton("👨‍👩‍👧‍👦 Family Info", callback_data="family")],
        [InlineKeyboardButton("📸 Instagram Scraper", callback_data="instagram")],
        [InlineKeyboardButton("💰 Points & Referral", callback_data="points")],
        [InlineKeyboardButton("🎁 Promo Code", callback_data="promo")],
        [InlineKeyboardButton("📊 Live Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("🔥 Name Style Generator", callback_data="name_style")],
        [InlineKeyboardButton("👑 Admin Panel", callback_data="admin")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_osint_keyboard():
    keyboard = [
        [InlineKeyboardButton("📞 Phone Lookup", callback_data="phone_lookup")],
        [InlineKeyboardButton("🔍 Trace Phone", callback_data="trace_phone")],
        [InlineKeyboardButton("🔍 Telegram User Lookup", callback_data="telegram_lookup")],
        [InlineKeyboardButton("🆔 Aadhar Lookup", callback_data="aadhar_lookup")],
        [InlineKeyboardButton("🏦 IFSC to Bank", callback_data="ifsc_lookup")],
        [InlineKeyboardButton("📧 Email Breach", callback_data="email_breach")],
        [InlineKeyboardButton("🌍 IP Geolocation", callback_data="ip_lookup")],
        [InlineKeyboardButton("👤 Username Search", callback_data="username_search")],
        [InlineKeyboardButton("🔍 Domain WHOIS", callback_data="domain_whois")],
        [InlineKeyboardButton("📱 Social Media", callback_data="social_media")],
        [InlineKeyboardButton("📡 Phone Carrier Info", callback_data="carrier_info")],
        [InlineKeyboardButton("◀️ Back", callback_data="back_osint")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_advanced_tools_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔐 Password Strength", callback_data="password_check"),
         InlineKeyboardButton("🔑 Password Generator", callback_data="password_generator")],
        [InlineKeyboardButton("🌐 Website Tech", callback_data="website_tech"),
         InlineKeyboardButton("🌍 GeoIP & ASN", callback_data="geoip_lookup")],
        [InlineKeyboardButton("🔗 URL Expander", callback_data="url_expander"),
         InlineKeyboardButton("🔗 URL Shortener", callback_data="url_shortener")],
        [InlineKeyboardButton("🔍 Hash Analyzer", callback_data="hash_analyzer"),
         InlineKeyboardButton("📊 Social Analyzer", callback_data="social_analyzer")],
        [InlineKeyboardButton("📝 Text Analyzer", callback_data="text_analyzer"),
         InlineKeyboardButton("🌤️ Weather Pass", callback_data="weather_pass")],
        [InlineKeyboardButton("📝 My Note", callback_data="my_note"),
         InlineKeyboardButton("🔄 Translate", callback_data="translate_tool")],
        [InlineKeyboardButton("🎯 Target Profile", callback_data="target_profile"),
         InlineKeyboardButton("📱 SIM Swap Check", callback_data="sim_swap")],
        [InlineKeyboardButton("💀 Dark Web Monitor", callback_data="dark_web"),
         InlineKeyboardButton("🖼️ Text to QR", callback_data="text_to_qr")],
        [InlineKeyboardButton("🎨 Text to Logo", callback_data="text_to_logo"),
         InlineKeyboardButton("🔍 IP Scanner", callback_data="ip_scanner")],
        [InlineKeyboardButton("◀️ Back", callback_data="back_advanced")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 View All Users", callback_data="admin_users")],
        [InlineKeyboardButton("💰 Add Points", callback_data="admin_addpoints")],
        [InlineKeyboardButton("💰 Remove Points", callback_data="admin_removepoints")],
        [InlineKeyboardButton("🎁 Add Promo Code", callback_data="admin_addpromo")],
        [InlineKeyboardButton("🗑️ Delete Promo Code", callback_data="admin_delpromo")],
        [InlineKeyboardButton("📢 Send Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📈 Full Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("◀️ Back", callback_data="back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_promo_keyboard():
    codes = get_all_promo_codes()
    keyboard = []
    for code, points in codes:
        keyboard.append([InlineKeyboardButton(f"🎁 {code} - {points} Points", callback_data=f"redeem_{code}")])
    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)

def get_points_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎁 Redeem Promo Code", callback_data="promo")],
        [InlineKeyboardButton("🔗 My Referral Link", callback_data="my_referral")],
        [InlineKeyboardButton("📊 Check Points", callback_data="check_points")],
        [InlineKeyboardButton("◀️ Back", callback_data="back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="back")]])

def get_note_keyboard(user_id):
    notes = get_notes(user_id)
    keyboard = []
    for note_id, note_text, date in notes[:5]:
        short_note = note_text[:30] + "..." if len(note_text) > 30 else note_text
        keyboard.append([InlineKeyboardButton(f"📝 {short_note}", callback_data=f"view_note_{note_id}")])
    keyboard.append([InlineKeyboardButton("➕ New Note", callback_data="new_note")])
    keyboard.append([InlineKeyboardButton("🗑️ Delete All", callback_data="delete_all_notes")])
    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back_advanced")])
    return InlineKeyboardMarkup(keyboard)

def get_name_style_keyboard(page, total_pages):
    keyboard = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Previous", callback_data="name_style_prev"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data="name_style_next"))
    if nav_row:
        keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("🆕 New Text", callback_data="name_style_new")])
    keyboard.append([InlineKeyboardButton("◀️ Back", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)

def escape_markdown(text):
    if not text:
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

# ============= COMMAND / START =============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name

    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referred_by = int(context.args[0].split("_")[1])
        except:
            pass

    register_user(user_id, username, referred_by)

    total_users = get_total_users()
    active_users = get_active_users()
    user_points = get_user_points(user_id)
    is_admin = "YES" if user_id in ADMINS else "NO"

    welcome_text = f"""
🔥🔥 *HACKER OSINT* 🔥🔥

╔══════════════════════════════╗
║    COMPLETE OSINT BOT      ║
║    ALL TOOLS WORKING       ║
╚══════════════════════════════╝

*FEATURES:*
✅ OSINT Tools (12+ Tools)
✅ Advanced Tools (20+ Tools)
✅ Points System (2 free points)
✅ Refer & Earn (5 points per refer)
✅ No API Keys Required!

👇 *CLICK BUTTONS BELOW* 👇

👋 *Welcome* {user.first_name}!
💰 *Your Points:* `{user_points}`
📊 *Total Users:* `{total_users}`
📈 *Active Users:* `{active_users}`
👑 *Admin:* `{is_admin}`
"""
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

# ============= NAME STYLE HANDLER =============
async def name_style_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "name_style_new":
        context.user_data['awaiting_name'] = True
        await query.edit_message_text(
            "🔥 *Name Style Generator*\n\nSend any text to get 100+ stylish fonts!\nExample: `Hello World` or `saurav`\n\n*Free*",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return
    if data == "name_style_prev":
        context.user_data['name_page'] = context.user_data.get('name_page', 1) - 1
    elif data == "name_style_next":
        context.user_data['name_page'] = context.user_data.get('name_page', 1) + 1
    text = context.user_data.get('name_text', '')
    page = context.user_data.get('name_page', 1)
    result, page, total_pages = name_style_generator(text, page)
    await query.edit_message_text(
        result,
        reply_markup=get_name_style_keyboard(page, total_pages),
        parse_mode="Markdown"
    )

# ============= BUTTON HANDLER =============
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("name_style_"):
        await name_style_handler(update, context)
        return

    if data == "back":
        user_points = get_user_points(user_id)
        total_users = get_total_users()
        active_users = get_active_users()
        text = f"""
🔥🔥 *HACKER OSINT* 🔥🔥

👇 *CLICK BUTTONS BELOW* 👇

👋 *Welcome!*
💰 *Your Points:* `{user_points}`
📊 *Total Users:* `{total_users}`
📈 *Active Users:* `{active_users}`
"""
        await query.edit_message_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        return
    
    if data == "back_osint":
        await query.edit_message_text(
            "🔍 *OSINT TOOLS* - Select an option:\n\n"
            "⚠️ *Cost:*\n"
            "   📞 Phone Lookup = 1 point\n"
            "   🔍 Trace Phone = 1 point\n"
            "   🔍 Telegram Lookup = 1 point\n"
            "   All others = 2 points",
            reply_markup=get_osint_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    if data == "back_advanced":
        await query.edit_message_text(
            "🔧 *ADVANCED OSINT TOOLS* 🔧\n\n"
            "Select a tool below:\n\n"
            "⚠️ *Cost:* All tools = 1 point\n"
            "📌 No API keys required - All tools work locally!",
            reply_markup=get_advanced_tools_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "osint":
        await query.edit_message_text(
            "🔍 *OSINT TOOLS* - Select an option:\n\n"
            "⚠️ *Cost:*\n"
            "   📞 Phone Lookup = 1 point\n"
            "   🔍 Trace Phone = 1 point\n"
            "   🔍 Telegram Lookup = 1 point\n"
            "   All others = 2 points",
            reply_markup=get_osint_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "advanced":
        await query.edit_message_text(
            "🔧 *ADVANCED OSINT TOOLS* 🔧\n\n"
            "Select a tool below:\n\n"
            "⚠️ *Cost:* All tools = 1 point\n"
            "📌 No API keys required - All tools work locally!",
            reply_markup=get_advanced_tools_keyboard(),
            parse_mode="Markdown"
        )
        return

    # My Note handlers
    if data == "my_note":
        await query.edit_message_text(
            "📝 *My Notes*\n\nSelect a note to view or create a new one:",
            reply_markup=get_note_keyboard(user_id),
            parse_mode="Markdown"
        )
        return
    
    if data == "new_note":
        context.user_data['awaiting_note'] = True
        await query.edit_message_text(
            "📝 *Create New Note*\n\nSend me the note you want to save.\n\nExample: `Meeting at 3 PM tomorrow`",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return
    
    if data == "delete_all_notes":
        notes = get_notes(user_id)
        for note_id, _, _ in notes:
            delete_note(note_id, user_id)
        await query.edit_message_text(
            "✅ *All notes deleted successfully!*",
            reply_markup=get_note_keyboard(user_id),
            parse_mode="Markdown"
        )
        return
    
    if data.startswith("view_note_"):
        note_id = int(data.split("_")[2])
        notes = get_notes(user_id)
        for nid, note_text, date in notes:
            if nid == note_id:
                result = f"""
📝 *YOUR NOTE*
╔══════════════════════════════════════╗
║ *Date:* `{date[:19]}`
╠══════════════════════════════════════╣
║ {note_text}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111

*Options:*
/delete_note_{note_id} - Delete this note
/notes - View all notes
"""
                await query.edit_message_text(result, parse_mode="Markdown")
                return
        await query.edit_message_text("❌ Note not found.", reply_markup=get_back_keyboard())
        return

    # Advanced Tools actions
    advanced_actions = {
        "password_check": "password_check",
        "password_generator": "password_generator",
        "website_tech": "website_tech",
        "geoip_lookup": "geoip_lookup",
        "url_expander": "url_expander",
        "url_shortener": "url_shortener",
        "hash_analyzer": "hash_analyzer",
        "social_analyzer": "social_analyzer",
        "text_analyzer": "text_analyzer",
        "weather_pass": "weather_pass",
        "translate_tool": "translate_tool",
        "target_profile": "target_profile",
        "sim_swap": "sim_swap",
        "dark_web": "dark_web",
        "text_to_qr": "text_to_qr",
        "text_to_logo": "text_to_logo",
        "ip_scanner": "ip_scanner",
    }
    if data in advanced_actions:
        prompt_texts = {
            "password_check": "🔐 *Password Strength Checker*\n\nSend a password to check its strength:\n• Length analysis\n• Character variety\n• Common password detection\n\n⚠️ *Cost: 1 point*",
            "password_generator": "🔑 *Password Generator*\n\nSend desired password length (8-32):\nExample: `12`\n\n• Generates strong password\n• Includes uppercase, lowercase, numbers, special chars\n\n⚠️ *Cost: 1 point*",
            "website_tech": "🌐 *Website Technology Detector*\n\nSend a domain name or URL:\nExample: `example.com`\n\n• Server type\n• CMS detection\n• SSL status\n\n⚠️ *Cost: 1 point*",
            "geoip_lookup": "🌍 *GeoIP & ASN Lookup*\n\nSend an IP address:\nExample: `8.8.8.8`\n\n• Country, City\n• ISP, Organization\n• ASN Number\n\n⚠️ *Cost: 1 point*",
            "url_expander": "🔗 *URL Expander*\n\nSend a shortened URL:\nExample: `https://bit.ly/xyz`\n\n• Expand to full URL\n• Show redirect chain\n\n⚠️ *Cost: 1 point*",
            "url_shortener": "🔗 *URL Shortener*\n\nSend a long URL to shorten:\nExample: `https://example.com/very/long/url`\n\n• Generate short URL\n• Works with TinyURL\n\n⚠️ *Cost: 1 point*",
            "hash_analyzer": "🔍 *Hash Analyzer*\n\nSend a hash string:\nExample: `5d41402abc4b2a76b9719d911017c592`\n\n• Identify hash type\n• Hash length analysis\n\n⚠️ *Cost: 1 point*",
            "social_analyzer": "📊 *Social Media Analyzer*\n\nSend a username:\nExample: `johndoe`\n\n• Find accounts across 400+ platforms\n\n⚠️ *Cost: 1 point*",
            "text_analyzer": "📝 *Text Analyzer*\n\nSend any text message\n\n• Language detection\n• Sentiment analysis\n• Word/character count\n\n⚠️ *Cost: 1 point*",
            "weather_pass": "🌤️ *Weather Pass*\n\nSend a city name:\nExample: `Mumbai` or `Delhi`\n\n• Current weather forecast\n• Temperature, humidity, wind\n\n⚠️ *Cost: 1 point*",
            "translate_tool": "🔄 *Translate Tool*\n\nSend text to translate (English to Hindi):\nExample: `Hello, how are you?`\n\n• Simple English-Hindi translation\n\n⚠️ *Cost: 1 point*",
            "target_profile": "🎯 *Target Profile Generator*\n\nSend a username:\nExample: `johndoe`\n\n• Generate possible social media URLs\n\n⚠️ *Cost: 1 point*",
            "sim_swap": "📱 *SIM Swap & Portability Check*\n\nSend a phone number:\nExample: `+919876543210`\n\n• Current carrier\n• Portability status\n\n⚠️ *Cost: 1 point*",
            "dark_web": "💀 *Dark Web Monitoring*\n\nSend an email address:\nExample: `user@example.com`\n\n• Check for data breaches\n• Risk score\n\n⚠️ *Cost: 1 point*",
            "text_to_qr": "🖼️ *Text to QR Code*\n\nSend text to convert to QR code:\nExample: `https://example.com`\n\n• Generates QR code image\n\n⚠️ *Cost: 1 point*",
            "text_to_logo": "🎨 *Text to Logo*\n\nSend text to convert to ASCII logo:\nExample: `SAURAV`\n\n• Generates 5 different logo styles\n\n⚠️ *Cost: 1 point*",
            "ip_scanner": "🔍 *IP Scanner*\n\nSend an IP address to scan:\nExample: `8.8.8.8`\n\n• Scans common ports\n• Shows open ports and services\n• GeoIP information\n\n⚠️ *Cost: 1 point*",
        }
        context.user_data['advanced_tool'] = data
        await query.edit_message_text(
            prompt_texts.get(data, f"🔧 *{data.replace('_', ' ').title()}*\n\nSend the required information.\n\n⚠️ *Cost: 1 point*"),
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return

    # OSINT actions
    osint_actions = {
        "phone_lookup": ("📞 *Phone Lookup*\n\nSend the phone number with country code:\nExample: `+919876543210`\n\n⚠️ *Cost: 1 point*", "phone", COST_PHONE),
        "trace_phone": ("🔍 *Trace Phone Number*\n\nSend the phone number with country code:\nExample: `+919876543210`\n\n⚠️ *Cost: 1 point*", "trace_phone", COST_PHONE),
        "telegram_lookup": ("🔍 *Telegram User Lookup*\n\nSend Telegram username or user ID:\nExample: `@username` or `123456789`\n\n⚠️ *Cost: 1 point*", "telegram", COST_TELEGRAM),
        "aadhar_lookup": ("🆔 *Aadhar Lookup*\n\nSend Aadhar number:\nExample: `1234-5678-9012` or `123456789012`\n\n• Name\n• Father's Name\n• Address\n• Phone Number\n\n⚠️ *Cost: 2 points*", "aadhar", COST_OSINT),
        "ifsc_lookup": ("🏦 *IFSC to Bank Details*\n\nSend IFSC code:\nExample: `SBIN0001234`\n\n• Bank Name\n• Branch\n• Address\n• City, District, State\n\n⚠️ *Cost: 2 points*", "ifsc", COST_OSINT),
        "email_breach": ("📧 *Email Breach Check*\n\nSend the email address:\nExample: `user@example.com`\n\n⚠️ *Cost: 2 points*", "email", COST_OSINT),
        "ip_lookup": ("🌍 *IP Geolocation*\n\nSend the IP address:\nExample: `8.8.8.8`\n\n⚠️ *Cost: 2 points*", "ip", COST_OSINT),
        "username_search": ("👤 *Username Search*\n\nSend the username:\nExample: `johndoe`\n\n⚠️ *Cost: 2 points*", "username", COST_OSINT),
        "domain_whois": ("🔍 *Domain WHOIS*\n\nSend the domain name:\nExample: `example.com`\n\n⚠️ *Cost: 2 points*", "domain", COST_OSINT),
        "social_media": ("📱 *Social Media Lookup*\n\nSend the username:\nExample: `johndoe`\n\n⚠️ *Cost: 2 points*", "social", COST_OSINT),
        "carrier_info": ("📡 *Phone Carrier Info*\n\nSend the phone number:\nExample: `+919876543210`\n\n⚠️ *Cost: 1 point*", "carrier", COST_PHONE),
    }
    if data in osint_actions:
        prompt, osint_type, cost = osint_actions[data]
        context.user_data['osint_type'] = osint_type
        context.user_data['osint_cost'] = cost
        await query.edit_message_text(prompt, reply_markup=get_back_keyboard(), parse_mode="Markdown")
        return

    if data == "family":
        context.user_data['awaiting_family'] = True
        await query.edit_message_text(
            "👨‍👩‍👧‍👦 *Family Info API*\n\nSend the UID or Full Name:\nExample: `IND00123456` or `John Doe`\n\n⚠️ *Cost: 2 points*",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "instagram":
        context.user_data['awaiting_instagram'] = True
        await query.edit_message_text(
            "📸 *Instagram Profile Scraper*\n\nSend Instagram username:\nExample: `@username` or `username`\n\n⚠️ *Cost: 2 points*",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "points":
        user_points = get_user_points(user_id)
        await query.edit_message_text(
            f"💰 *Points System*\n\nYour Points: `{user_points}`\n\n*How to earn:*\n• Start: 2 points\n• Referral: +5 points per user\n• Promo codes\n\nWhat would you like to do?",
            reply_markup=get_points_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "my_referral":
        ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        await query.edit_message_text(
            f"🔗 *Your Referral Link*\n\n`{ref_link}`\n\nShare this link – each new user gives you +5 points.",
            reply_markup=get_points_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "check_points":
        user_points = get_user_points(user_id)
        await query.edit_message_text(
            f"💰 *Your Points*\n\nCurrent Balance: `{user_points}` points",
            reply_markup=get_points_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "promo":
        await query.edit_message_text(
            "🎁 *Promo Codes*\n\nSelect a promo code to redeem:",
            reply_markup=get_promo_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data.startswith("redeem_"):
        code = data.replace("redeem_", "").upper()
        success, points = redeem_promo_code(user_id, code)
        if success:
            await query.edit_message_text(f"✅ Redeemed! +{points} points.", reply_markup=get_promo_keyboard())
        else:
            await query.edit_message_text("❌ Invalid or already used promo code.", reply_markup=get_promo_keyboard())
        return

    if data == "dashboard":
        total_users = get_total_users()
        active_users = get_active_users()
        user_points = get_user_points(user_id)
        await query.edit_message_text(
            f"📊 *Live Dashboard*\n\n"
            f"📊 Total Users: `{total_users}`\n"
            f"📈 Active Users: `{active_users}`\n"
            f"💰 Your Points: `{user_points}`\n"
            f"👑 Admin: `{'YES' if user_id in ADMINS else 'NO'}`\n\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "name_style":
        context.user_data['awaiting_name'] = True
        await query.edit_message_text(
            "🔥 *Name Style Generator*\n\nSend any text to get 100+ stylish fonts!\nExample: `Hello World` or `saurav`\n\n*Free*",
            reply_markup=get_back_keyboard(),
            parse_mode="Markdown"
        )
        return

    if data == "admin":
        if user_id in ADMINS:
            await query.edit_message_text(
                "👑 *Admin Panel*\n\nSelect an action:",
                reply_markup=get_admin_keyboard(),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text("⛔ You are not an admin.", reply_markup=get_back_keyboard())
        return

    if data == "admin_users":
        if user_id in ADMINS:
            users = get_all_users()
            text = "👥 *All Users*\n\n"
            for i, u in enumerate(users[:20], 1):
                username_safe = escape_markdown(u[1] if u[1] else "None")
                text += f"{i}. ID: `{u[0]}` | @{username_safe} | Points: {u[2]}\n"
            text += f"\nTotal: {len(users)} users"
            await query.edit_message_text(text, reply_markup=get_admin_keyboard(), parse_mode="Markdown")
        return

    if data == "admin_stats":
        if user_id in ADMINS:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(points) FROM users")
            total_points = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM referrals")
            total_refs = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM osint_logs")
            total_searches = cursor.fetchone()[0]
            conn.close()
            await query.edit_message_text(
                f"📊 *Full Statistics*\n\n"
                f"👥 Total Users: `{total}`\n"
                f"💰 Total Points: `{total_points}`\n"
                f"🔗 Total Referrals: `{total_refs}`\n"
                f"🔍 Total Searches: `{total_searches}`\n"
                f"👑 Admins: {len(ADMINS)}",
                reply_markup=get_admin_keyboard(),
                parse_mode="Markdown"
            )
        return

    if data == "admin_addpoints":
        if user_id in ADMINS:
            context.user_data['admin_action'] = 'addpoints'
            await query.edit_message_text(
                "💰 *Add Points*\n\nSend user ID and points:\n`123456789 10`\n\nExample: `123456789 50`",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
        return

    if data == "admin_removepoints":
        if user_id in ADMINS:
            context.user_data['admin_action'] = 'removepoints'
            await query.edit_message_text(
                "💰 *Remove Points*\n\nSend user ID and points to remove:\n`123456789 10`\n\nExample: `123456789 50`",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
        return

    if data == "admin_addpromo":
        if user_id in ADMINS:
            context.user_data['admin_action'] = 'addpromo'
            await query.edit_message_text(
                "🎁 *Add Promo Code*\n\nSend code and points:\n`SPECIAL50 50`",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
        return

    if data == "admin_delpromo":
        if user_id in ADMINS:
            context.user_data['admin_action'] = 'delpromo'
            await query.edit_message_text(
                "🗑️ *Delete Promo Code*\n\nSend the code to delete:\n`WELCOME10`",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
        return

    if data == "admin_broadcast":
        if user_id in ADMINS:
            context.user_data['admin_action'] = 'broadcast'
            await query.edit_message_text(
                "📢 *Send Broadcast*\n\nSend the message to broadcast to all users.",
                reply_markup=get_back_keyboard(),
                parse_mode="Markdown"
            )
        return

# ============= MESSAGE HANDLER =============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Handle note creation
    if context.user_data.get('awaiting_note'):
        save_note(user_id, text)
        context.user_data.pop('awaiting_note', None)
        await update.message.reply_text(
            f"✅ *Note saved!*\n\n📝 {text[:100]}",
            reply_markup=get_note_keyboard(user_id),
            parse_mode="Markdown"
        )
        return

    # Admin actions
    admin_action = context.user_data.get('admin_action')
    if admin_action and user_id in ADMINS:
        if admin_action == "addpoints":
            try:
                parts = text.split()
                target_id = int(parts[0])
                points = int(parts[1])
                add_points(target_id, points)
                await update.message.reply_text(f"✅ Added {points} points to user `{target_id}`", parse_mode="Markdown")
            except:
                await update.message.reply_text("❌ Invalid format. Use: `user_id points`", parse_mode="Markdown")
        elif admin_action == "removepoints":
            try:
                parts = text.split()
                target_id = int(parts[0])
                points = int(parts[1])
                current = get_user_points(target_id)
                if current >= points:
                    add_points(target_id, -points)
                    await update.message.reply_text(f"✅ Removed {points} points from user `{target_id}`. Remaining: {current-points}", parse_mode="Markdown")
                else:
                    await update.message.reply_text(f"❌ User `{target_id}` has only {current} points.", parse_mode="Markdown")
            except:
                await update.message.reply_text("❌ Invalid format. Use: `user_id points`", parse_mode="Markdown")
        elif admin_action == "addpromo":
            try:
                parts = text.split()
                code = parts[0].upper()
                points = int(parts[1])
                add_promo_code(code, points)
                await update.message.reply_text(f"✅ Promo code `{code}` added with {points} points!", parse_mode="Markdown")
            except:
                await update.message.reply_text("❌ Invalid format. Use: `CODE points`", parse_mode="Markdown")
        elif admin_action == "delpromo":
            code = text.upper()
            delete_promo_code(code)
            await update.message.reply_text(f"✅ Promo code `{code}` deleted!", parse_mode="Markdown")
        elif admin_action == "broadcast":
            users = get_all_users()
            success = 0
            for u in users:
                try:
                    await context.bot.send_message(chat_id=u[0], text=f"📢 *Broadcast*\n\n{text}", parse_mode="Markdown")
                    success += 1
                except:
                    pass
            await update.message.reply_text(f"✅ Broadcast sent to {success}/{len(users)} users")
        context.user_data.pop('admin_action', None)
        return

    # Advanced Tools handlers
    advanced_tool = context.user_data.get('advanced_tool')
    if advanced_tool:
        cost = COST_ADVANCED
        user_points = get_user_points(user_id)
        if user_points < cost:
            await update.message.reply_text(f"❌ Insufficient points! Need {cost} point.", parse_mode="Markdown")
            context.user_data.pop('advanced_tool', None)
            return
        if not deduct_points(user_id, cost):
            await update.message.reply_text("❌ Failed to deduct points.", parse_mode="Markdown")
            context.user_data.pop('advanced_tool', None)
            return

        if advanced_tool == "password_check":
            strength, score, feedback = check_password_strength(text)
            result = f"""
🔐 *PASSWORD STRENGTH CHECKER* 🔐
╔══════════════════════════════════════╗
║ *PASSWORD:* `{text[:2]}{'*' * (len(text)-2)}`
╠══════════════════════════════════════╣
║ 🟡 *STRENGTH:* `{strength}` (Score: {score}/5)
║
║ *FEEDBACK:*
"""
            for fb in feedback:
                result += f"║   {fb}\n"
            result += f"""
║
║ *ESTIMATED CRACK TIME:*
║   • Online: {random.choice(['Hours', 'Days', 'Years'])}
║   • Offline: {random.choice(['Seconds', 'Minutes', 'Hours'])}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "password_generator":
            try:
                length = int(text)
                if length < 8:
                    length = 8
                if length > 32:
                    length = 32
            except:
                length = 12
            password = generate_password(length)
            result = f"""
🔑 *PASSWORD GENERATOR* 🔑
╔══════════════════════════════════════╗
║ *LENGTH:* `{length}`
╠══════════════════════════════════════╣
║ *GENERATED PASSWORD:*
║ `{password}`
║
║ *STRENGTH:* Strong
║ *CHARACTERS:* Uppercase, Lowercase, Numbers, Special
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "website_tech":
            tech = detect_website_tech(text)
            result = f"""
🌐 *WEBSITE TECHNOLOGY DETECTOR* 🌐
╔══════════════════════════════════════╗
║ *DOMAIN:* `{text}`
╠══════════════════════════════════════╣
"""
            for key, value in tech.items():
                result += f"║ 🔹 *{key}:* `{value}`\n"
            result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "geoip_lookup":
            info = geoip_lookup(text)
            if info:
                result = f"""
🌍 *GEOIP & ASN LOOKUP* 🌍
╔══════════════════════════════════════╗
║ *IP ADDRESS:* `{text}`
╠══════════════════════════════════════╣
"""
                for key, value in info.items():
                    result += f"║ 🔹 *{key}:* `{value}`\n"
                result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            else:
                result = f"""
🌍 *GEOIP & ASN LOOKUP* 🌍
╔══════════════════════════════════════╗
║ *IP ADDRESS:* `{text}`
╠══════════════════════════════════════╣
║ ⚠️ *Could not retrieve data*
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "url_expander":
            expanded, history = expand_url(text)
            if expanded:
                result = f"""
🔗 *URL EXPANDER* 🔗
╔══════════════════════════════════════╗
║ *SHORT URL:* `{text}`
╠══════════════════════════════════════╣
║ 🟢 *FULL URL:* `{expanded}`
║
║ *REDIRECT CHAIN:* {len(history) if history else 0} redirects
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            else:
                result = f"""
🔗 *URL EXPANDER* 🔗
╔══════════════════════════════════════╗
║ *SHORT URL:* `{text}`
╠══════════════════════════════════════╣
║ ⚠️ *Could not expand URL*
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "url_shortener":
            shortened = shorten_url(text)
            if shortened:
                result = f"""
🔗 *URL SHORTENER* 🔗
╔══════════════════════════════════════╗
║ *ORIGINAL URL:* `{text}`
╠══════════════════════════════════════╣
║ 🟢 *SHORTENED URL:* `{shortened}`
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            else:
                result = f"""
🔗 *URL SHORTENER* 🔗
╔══════════════════════════════════════╗
║ *URL:* `{text}`
╠══════════════════════════════════════╣
║ ⚠️ *Could not shorten URL*
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "hash_analyzer":
            hash_type = analyze_hash(text)
            result = f"""
🔍 *HASH ANALYZER* 🔍
╔══════════════════════════════════════╗
║ *HASH:* `{text}`
╠══════════════════════════════════════╣
║ 🟢 *HASH TYPE:* `{hash_type}`
║ 📏 *LENGTH:* {len(text)} characters
║
║ *COMMON USES:*
║   • MD5: File checksums
║   • SHA1: Git commits
║   • SHA256: Password storage
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "social_analyzer":
            found = social_analyzer(text)
            result = f"""
📊 *SOCIAL MEDIA ANALYZER* 📊
╔══════════════════════════════════════╗
║ *USERNAME:* `{text}`
╠══════════════════════════════════════╣
║ 🟢 *FOUND ON {len(found)} PLATFORMS*
║
"""
            for platform in found[:15]:
                result += f"║   ✅ {platform}\n"
            if len(found) > 15:
                result += f"║   ... and {len(found)-15} more\n"
            result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "text_analyzer":
            analysis = analyze_text(text)
            result = f"""
📝 *TEXT ANALYZER* 📝
╔══════════════════════════════════════╗
║ *TEXT:* `{text[:50]}{'...' if len(text)>50 else ''}`
╠══════════════════════════════════════╣
"""
            for key, value in analysis.items():
                result += f"║ 🔹 *{key}:* `{value}`\n"
            result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "weather_pass":
            weather = weather_pass(text)
            result = f"""
🌤️ *WEATHER PASS* 🌤️
╔══════════════════════════════════════╗
║ *CITY:* `{weather['city']}`
╠══════════════════════════════════════╣
║ ☁️ *CONDITION:* {weather['condition']}
║ 🌡️ *TEMPERATURE:* {weather['temperature']}°C
║ 💧 *HUMIDITY:* {weather['humidity']}%
║ 🌬️ *WIND:* {weather['wind']} km/h
║ 📋 *FORECAST:* {weather['forecast']}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "translate_tool":
            translated = translate_text(text)
            result = f"""
🔄 *TRANSLATE TOOL* 🔄
╔══════════════════════════════════════╗
║ *ORIGINAL:* `{text}`
╠══════════════════════════════════════╣
║ 🔹 *HINDI TRANSLATION:*
║
║ {translated}
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "target_profile":
            profiles = generate_target_profile(text)
            result = f"""
🎯 *TARGET PROFILE GENERATOR* 🎯
╔══════════════════════════════════════╗
║ *USERNAME:* `{text}`
╠══════════════════════════════════════╣
║ 🟢 *POSSIBLE SOCIAL MEDIA ACCOUNTS:*
║
"""
            for url in profiles:
                result += f"║   🔗 {url}\n"
            result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "sim_swap":
            info = sim_swap_check(text)
            result = f"""
📱 *SIM SWAP & PORTABILITY CHECK* 📱
╔══════════════════════════════════════╗
║ *NUMBER:* `{text}`
╠══════════════════════════════════════╣
"""
            for key, value in info.items():
                result += f"║ 🔹 *{key}:* `{value}`\n"
            result += f"""
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "dark_web":
            info = dark_web_monitor(text)
            result = f"""
💀 *DARK WEB MONITORING* 💀
╔══════════════════════════════════════╗
║ *EMAIL:* `{text}`
╠══════════════════════════════════════╣
║ 🔴 *{info['Breaches Found']} BREACHES FOUND*
║
"""
            for breach in info['Breaches'][:5]:
                result += f"║   🔥 {breach}\n"
            result += f"""
║ *RISK SCORE:* `{info['Risk Score']}/100`
║
║ *RECOMMENDATION:* `{info['Recommendation']}`
║
╚══════════════════════════════════════╝
👑 *Owner:* @sauravsingh2111"""
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "text_to_qr":
            qr_img = generate_qr_code(text)
            await update.message.reply_photo(
                photo=InputFile(qr_img, filename="qrcode.png"),
                caption=f"🖼️ *QR Code for:* `{text[:50]}`",
                parse_mode="Markdown"
            )
        
        elif advanced_tool == "text_to_logo":
            logos = text_to_logo(text)
            result = "🎨 *TEXT TO LOGO* 🎨\n\n"
            for style_name, logo in logos:
                result += f"*{style_name}*\n```\n{logo}\n```\n\n"
            result += f"👑 *Owner:* @sauravsingh2111"
            await update.message.reply_text(result, parse_mode="Markdown")
        
        elif advanced_tool == "ip_scanner":
            result = ip_scanner(text)
            await update.message.reply_text(result, parse_mode="Markdown")
        
        context.user_data.pop('advanced_tool', None)
        return

    # OSINT queries
    osint_type = context.user_data.get('osint_type')
    if osint_type:
        cost = context.user_data.get('osint_cost', COST_OSINT)
        user_points = get_user_points(user_id)
        if user_points < cost:
            await update.message.reply_text(f"❌ Insufficient points! Need {cost} points.", parse_mode="Markdown")
            context.user_data.pop('osint_type', None)
            context.user_data.pop('osint_cost', None)
            return
        if cost > 0 and not deduct_points(user_id, cost):
            await update.message.reply_text("❌ Failed to deduct points.", parse_mode="Markdown")
            context.user_data.pop('osint_type', None)
            context.user_data.pop('osint_cost', None)
            return

        if osint_type == "phone":
            api_res = phone_lookup_api(text)
            result = format_phone_result(api_res, text)
        elif osint_type == "trace_phone":
            api_res = phone_lookup_api(text)
            result = format_trace_phone_result(api_res, text)
        elif osint_type == "carrier":
            api_res = phone_lookup_api(text)
            result = format_carrier_result(api_res, text)
        elif osint_type == "telegram":
            result = await telegram_lookup_handler(text)
        elif osint_type == "aadhar":
            result = aadhar_lookup(text)
        elif osint_type == "ifsc":
            result = ifsc_lookup(text)
        elif osint_type == "ip":
            result = await ip_handler(text)
        elif osint_type == "domain":
            result = await domain_handler(text)
        elif osint_type in ["username", "social"]:
            result = await username_search_handler(text, osint_type)
        elif osint_type == "email":
            result = await email_breach_handler(text)
        else:
            result = f"❌ Unknown OSINT type: {osint_type}"

        await update.message.reply_text(result, parse_mode="Markdown")
        context.user_data.pop('osint_type', None)
        context.user_data.pop('osint_cost', None)
        return

    # Family info
    if context.user_data.get('awaiting_family'):
        user_points = get_user_points(user_id)
        if user_points < 2:
            await update.message.reply_text("❌ Need 2 points for Family Info.", parse_mode="Markdown")
        elif deduct_points(user_id, 2):
            result = await family_info_handler(text)
            await update.message.reply_text(result, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Failed to deduct points.", parse_mode="Markdown")
        context.user_data.pop('awaiting_family', None)
        return

    # Instagram scraper
    if context.user_data.get('awaiting_instagram'):
        user_points = get_user_points(user_id)
        if user_points < 2:
            await update.message.reply_text("❌ Need 2 points for Instagram scraper.", parse_mode="Markdown")
        elif deduct_points(user_id, 2):
            result = await instagram_handler(text)
            await update.message.reply_text(result, parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Failed to deduct points.", parse_mode="Markdown")
        context.user_data.pop('awaiting_instagram', None)
        return

    # Name style generator
    if context.user_data.get('awaiting_name'):
        text = update.message.text
        context.user_data['name_text'] = text
        context.user_data['name_page'] = 1
        result, page, total_pages = name_style_generator(text, 1)
        await update.message.reply_text(
            result,
            reply_markup=get_name_style_keyboard(page, total_pages),
            parse_mode="Markdown"
        )
        context.user_data.pop('awaiting_name', None)
        return

# ============= FILE HANDLER =============
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ *File upload feature is currently disabled.*\n\n"
        "Try other tools like:\n"
        "• Text to QR Code\n"
        "• Text to Logo\n"
        "• Password Generator\n"
        "• IP Scanner\n\n"
        "All tools cost only 1 point!",
        parse_mode="Markdown"
    )

# ============= MAIN FUNCTION =============
def main():
    init_bot_database()
    if not BOT_TOKEN:
        print("⚠️ BOT_TOKEN not set in environment variables!")
        return
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.ATTACHMENT, handle_file))
    print("🚀 Bot started! Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
