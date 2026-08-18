#!/usr/bin/env python3
# This file is a part of NEO-WZML (github.com/irisXDR/NEO-WZML)

import base64
from secrets import token_urlsafe
from cryptography.fernet import Fernet

print("=== Pyrogram Session String Encryption ===\n")

session_string = input("Enter your Pyrogram session string: ").strip()

if not session_string:
    print("Error: Session string cannot be empty!")
    exit(1)

fernet_key = Fernet.generate_key()

fernet = Fernet(fernet_key)
encrypted_session = fernet.encrypt(session_string.encode()).decode()

formatted_session = f"Fencrypted:{encrypted_session}"

print("\n=== ENCRYPTION COMPLETE ===\n")
print(f"Your Fernet Key (keep this safe!):\n{fernet_key.decode()}\n")
print(f"Your Encrypted Session String:\n{formatted_session}\n")
print("\n=== INSTRUCTIONS ===")
print("1. Set the Fernet Key as environment variable: SESSION_DECRYPT_KEY")
print(f"   export SESSION_DECRYPT_KEY={fernet_key.decode()}")
print("2. Set the Encrypted Session String as USER_SESSION_STRING in config")
print(f"   USER_SESSION_STRING={formatted_session}")
print("3. Restart the bot - it will automatically decrypt the session on startup\n")
print("WARNING: Keep your Fernet Key safe! If lost, you cannot recover the session!")
