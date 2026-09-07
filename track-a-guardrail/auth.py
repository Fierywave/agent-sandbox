import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from contracts.enums import Role

SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me-in-production")
ALGORITHM: str = "HS256"
DEFAULT_EXPIRY_MINUTES: int = 60

_ROLE_ORDER = [Role.VIEWER, Role.ANALYST, Role.OPERATOR, Role.ADMIN]
_bearer = HTTPBearer()
router = APIRouter(prefix="/auth", tags=["Authentication"])


class TokenClaims(BaseModel):
    sub: str
    role: Role
    exp: datetime


class TokenRequest(BaseModel):
    user_id: str
    role: Role


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    role: Role


def create_token(
    user_id: str,
    role: Role,
    expiry_minutes: int = DEFAULT_EXPIRY_MINUTES,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role.value if isinstance(role, Role) else role,
        "iat": now,
        "exp": now + timedelta(minutes=expiry_minutes),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> TokenClaims:
    try:
        raw = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or signature verification failed.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        role = Role(raw["role"])
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Token carries an unknown role: '{raw.get('role')}'.",
        )

    exp_val = raw["exp"]
    exp_dt = datetime.fromtimestamp(exp_val, tz=timezone.utc) if isinstance(exp_val, (int, float)) else exp_val

    return TokenClaims(
        sub=raw["sub"],
        role=role,
        exp=exp_dt,
    )


def get_current_claims(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> TokenClaims:
    return decode_token(credentials.credentials)


def require_role(minimum_role: Role | None = None):
    def _check(
        claims: Annotated[TokenClaims, Depends(get_current_claims)],
    ) -> TokenClaims:
        if minimum_role is not None:
            caller_rank = _ROLE_ORDER.index(claims.role)
            required_rank = _ROLE_ORDER.index(minimum_role)
            if caller_rank < required_rank:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Role '{claims.role.value}' is insufficient. "
                        f"This action requires at least '{minimum_role.value}'."
                    ),
                )
        return claims

    return _check


def issue_test_tokens() -> dict[str, str]:
    return {
        role.value: create_token(user_id=f"test-{role.value}", role=role)
        for role in Role
    }


@router.post("/token", response_model=TokenResponse)
def issue_token_endpoint(req: TokenRequest) -> TokenResponse:
    token = create_token(user_id=req.user_id, role=req.role)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=req.user_id,
        role=req.role,
    )


@router.get("/test-tokens")
def get_test_tokens() -> dict[str, str]:
    return issue_test_tokens()


@router.get("/me", response_model=TokenClaims)
def get_me(claims: TokenClaims = Depends(get_current_claims)) -> TokenClaims:
    return claims


if __name__ == "__main__":
    tokens = issue_test_tokens()
    for role_name, token in tokens.items():
        claims = decode_token(token)
        print(f"{role_name:10s} -> sub={claims.sub!r}, role={claims.role.value!r}, exp={claims.exp}")
