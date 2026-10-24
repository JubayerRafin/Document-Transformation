import hashlib, hmac, secrets

def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def password_hash(password):
    if not 12<=len(password)<=200:raise ValueError('Use a password of 12–200 characters.')
    salt=secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex()
def verify(password,stored):
    if len(password)>200:return False
    salt,expected=stored.split(':')
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex(),expected)
