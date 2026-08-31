"""D5 外部平台统一适配器。

适配器只负责真实外部 HTTP 协议与标准事件归一化；鉴权、项目权限和落库由路由层负责。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

HTTP_TIMEOUT = 15.0


class AdapterError(RuntimeError):
    """可安全返回给调用方的外部平台错误。"""


@dataclass(frozen=True)
class PlatformIdentity:
    external_account_id: str
    external_username: str
    access_token: str
    scopes: list[str]


@dataclass(frozen=True)
class StandardEvent:
    external_id: str
    event_type: str
    title: str
    description: str
    evidence_url: str | None
    occurred_at: str | None
    actor: str | None
    payload: dict[str, Any]


class PlatformAdapter(Protocol):
    platform: str

    def configured(self) -> bool: ...
    def oauth_start(self, state: str, redirect_uri: str) -> str: ...
    def exchange_code(self, code: str, redirect_uri: str) -> PlatformIdentity: ...
    def fetch_events(self, access_token: str, config: dict[str, Any]) -> list[StandardEvent]: ...


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise AdapterError(f"外部平台返回了无法解析的数据（HTTP {response.status_code}）") from exc
    if not response.is_success:
        raise AdapterError(f"外部平台返回 HTTP {response.status_code}")
    if not isinstance(data, dict):
        raise AdapterError("外部平台返回的数据结构不正确")
    return data


class FeishuAdapter:
    platform = "feishu"
    api_base = "https://open.feishu.cn/open-apis"
    authorize_base = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"

    def configured(self) -> bool:
        return bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))

    def oauth_start(self, state: str, redirect_uri: str) -> str:
        app_id = os.getenv("FEISHU_APP_ID")
        if not self.configured() or not app_id:
            raise AdapterError("飞书应用凭据未配置")
        return f"{self.authorize_base}?{urlencode({'app_id': app_id, 'redirect_uri': redirect_uri, 'state': state})}"

    def exchange_code(self, code: str, redirect_uri: str) -> PlatformIdentity:
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            raise AdapterError("飞书应用凭据未配置")
        app_response = httpx.post(
            f"{self.api_base}/auth/v3/app_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=HTTP_TIMEOUT,
        )
        app_data = _json(app_response)
        app_token = app_data.get("app_access_token")
        if not app_token:
            raise AdapterError("飞书未返回 app_access_token")
        token_response = httpx.post(
            f"{self.api_base}/authen/v1/access_token",
            headers={"Authorization": f"Bearer {app_token}"},
            json={"grant_type": "authorization_code", "code": code},
            timeout=HTTP_TIMEOUT,
        )
        token_data = _json(token_response).get("data") or {}
        access_token = token_data.get("access_token")
        if not access_token:
            raise AdapterError("飞书未返回 user_access_token")
        user_response = httpx.get(
            f"{self.api_base}/authen/v1/user_info",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT,
        )
        user_data = _json(user_response).get("data") or {}
        account_id = user_data.get("open_id") or user_data.get("union_id")
        if not account_id:
            raise AdapterError("飞书未返回用户标识")
        return PlatformIdentity(
            external_account_id=str(account_id),
            external_username=str(user_data.get("name") or account_id),
            access_token=str(access_token),
            scopes=["wiki:read", "docx:read"],
        )

    def fetch_events(self, access_token: str, config: dict[str, Any]) -> list[StandardEvent]:
        resource_type = str(config.get("resource_type") or "document")
        resource_id = str(config.get("resource_id") or "").strip()
        if not resource_id:
            raise AdapterError("飞书项目集成缺少 resource_id")
        headers = {"Authorization": f"Bearer {access_token}"}
        if resource_type == "wiki_space":
            response = httpx.get(
                f"{self.api_base}/wiki/v2/spaces/{resource_id}/nodes",
                headers=headers,
                params={"page_size": 50},
                timeout=HTTP_TIMEOUT,
            )
            data = _json(response).get("data") or {}
            items = data.get("items") or []
        else:
            response = httpx.get(
                f"{self.api_base}/docx/v1/documents/{resource_id}",
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            document = (_json(response).get("data") or {}).get("document") or {}
            items = [document]
        events: list[StandardEvent] = []
        for item in items:
            token = str(item.get("node_token") or item.get("document_id") or item.get("obj_token") or "").strip()
            if not token:
                continue
            title = str(item.get("title") or item.get("obj_token") or "飞书文档更新")
            revision = str(item.get("revision_id") or item.get("obj_edit_time") or item.get("create_time") or "unknown")
            occurred_at = item.get("obj_edit_time") or item.get("create_time")
            events.append(StandardEvent(
                external_id=f"feishu:{token}:{revision}",
                event_type="document_updated",
                title=title[:200],
                description="由飞书云文档真实接口同步",
                evidence_url=config.get("resource_url"),
                occurred_at=str(occurred_at) if occurred_at else None,
                actor=None,
                payload={"resource_type": resource_type, "resource_id": resource_id, "item": item},
            ))
        return events


class TencentDocAdapter:
    platform = "tencent_doc"

    def configured(self) -> bool:
        return bool(os.getenv("TENCENT_DOC_APP_ID") and os.getenv("TENCENT_DOC_API_BASE"))

    def oauth_start(self, state: str, redirect_uri: str) -> str:
        raise AdapterError("腾讯文档当前账号未提供可用的 OAuth 配置；请使用平台连接凭据")

    def exchange_code(self, code: str, redirect_uri: str) -> PlatformIdentity:
        raise AdapterError("腾讯文档当前账号未提供可用的 OAuth 配置")

    def fetch_events(self, access_token: str, config: dict[str, Any]) -> list[StandardEvent]:
        api_base = (os.getenv("TENCENT_DOC_API_BASE") or "").rstrip("/")
        api_path = str(config.get("api_path") or "").strip()
        resource_id = str(config.get("resource_id") or "").strip()
        if not api_base or not api_path or not resource_id:
            raise AdapterError("腾讯文档需要配置 TENCENT_DOC_API_BASE、api_path 与 resource_id")
        response = httpx.get(
            f"{api_base}/{api_path.lstrip('/')}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"resource_id": resource_id},
            timeout=HTTP_TIMEOUT,
        )
        data = _json(response)
        raw_items = data.get("items") or data.get("data") or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        events: list[StandardEvent] = []
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("doc_id") or resource_id)
            version = str(item.get("version") or item.get("updated_at") or item.get("modify_time") or "unknown")
            events.append(StandardEvent(
                external_id=f"tencent_doc:{item_id}:{version}",
                event_type="document_updated",
                title=str(item.get("title") or "腾讯文档更新")[:200],
                description="由腾讯文档开放接口同步",
                evidence_url=str(item.get("url") or config.get("resource_url") or "") or None,
                occurred_at=str(item.get("updated_at") or item.get("modify_time") or "") or None,
                actor=str(item.get("editor") or "") or None,
                payload={"resource_id": resource_id, "item": item},
            ))
        return events


ADAPTERS: dict[str, PlatformAdapter] = {
    "feishu": FeishuAdapter(),
    "tencent_doc": TencentDocAdapter(),
}