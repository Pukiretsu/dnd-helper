from datetime import datetime, timedelta, UTC
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, Request, Cookie

from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, TAGS
from database import get_user_by_username, get_username_by_uuid

# Configure the password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """
    Creates a new JWT access token.
    Args:
        data (dict): Data to encode into the token (e.g., {"sub": user_uuid}).
        expires_delta (timedelta | None): Optional timedelta for token expiration.
    Returns:
        str: The encoded JWT token.
    """
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    """
    Verifies a JWT token and returns the subject (user_uuid) if valid.
    Args:
        token (str): The JWT token to verify.
    Returns:
        str | None: The user_uuid if the token is valid, otherwise None.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def get_current_user(request: Request):
    """
    Retrieves the current user's UUID from the access token cookie.
    Raises HTTPException if the user is not authenticated.
    """
    token = request.cookies.get("access_token")
    user_uuid = verify_token(token) if token else None
    if not user_uuid:
        raise HTTPException(status_code=303, detail="No autenticado", headers={"Location": "/login"})
    return user_uuid

async def authenticate_user(username: str, password: str):
    """Authenticates a user by checking username and password."""
    user_data = await get_user_by_username(username)
    if not user_data:
        print(f"{TAGS['app_error']} Usuario: {username} no existe.")
        return None, "El usuario no existe"

    hashed_password_from_db, user_uuid = user_data
    if not pwd_context.verify(password, hashed_password_from_db):
        print(f"{TAGS['app_error']} Usuario: {username} o contraseña invalidos.")
        return None, "Usuario o contraseña inválidos"
    
    return user_uuid, None # Return UUID and no error message on success

async def get_user_info_for_logout(request: Request):
    """Retrieves user info for logging purposes during logout."""
    token = request.cookies.get("access_token")
    user_uuid = None
    username = "Unknown User"

    if token:
        user_uuid = verify_token(token)
        if user_uuid:
            username = await get_username_by_uuid(user_uuid) or username
    return user_uuid, username
