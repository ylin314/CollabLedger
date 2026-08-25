from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse, Response

from backend.auth import COOKIE_NAME, create_session, hash_password, iso_utc, revoke_session, verify_password
from backend.core.context import *
from backend.schemas import *

router = APIRouter()

@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "collab-ledger"}

__all__ = ['health']
