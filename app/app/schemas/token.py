from __future__ import annotations

from pydantic import BaseModel, ConfigDict, SecretStr
from odmantic import Model, ObjectId


class RefreshTokenBase(BaseModel):
    token: SecretStr
    authenticates: Model | None = None


class RefreshTokenCreate(RefreshTokenBase):
    authenticates: Model


class RefreshTokenUpdate(RefreshTokenBase):
    pass


class RefreshToken(RefreshTokenUpdate):
    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str


class TokenPayload(BaseModel):
    sub: ObjectId | None = None
    refresh: bool | None = False
    totp: bool | None = False


class MagicTokenPayload(BaseModel):
    sub: ObjectId | None = None
    fingerprint: ObjectId | None = None


class WebToken(BaseModel):
    claim: str

class ApiKey(BaseModel):
    api_key: str
    key_id: str
    days: int
    only_chat: bool
    name: str
    creation_time: str
    is_active: bool