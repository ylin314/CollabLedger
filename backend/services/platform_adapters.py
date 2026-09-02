"""D5 外部平台统一适配器。

适配器只负责真实外部 HTTP 协议与标准事件归一化；鉴权、项目权限和落库由路由层负责。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote, urlencode, urlparse

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
    default_api_base = "https://docs.qq.com"
    default_metadata_path = "/openapi/drive/v2/files/{fileID}/metadata"

    @classmethod
    def _api_base(cls) -> str:
        raw = (os.getenv("TENCENT_DOC_API_BASE") or cls.default_api_base).strip()
        parsed = urlparse(raw)
        try:
            port = parsed.port
        except ValueError as exc:
            raise AdapterError("腾讯文档 API 根地址必须是 https://docs.qq.com") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != "docs.qq.com"
            or parsed.username
            or parsed.password
            or port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise AdapterError("腾讯文档 API 根地址必须是 https://docs.qq.com")
        return cls.default_api_base

    def configured(self) -> bool:
        try:
            self._api_base()
        except AdapterError:
            return False
        return bool(os.getenv("TENCENT_DOC_APP_ID") and os.getenv("TENCENT_DOC_API_BASE"))

    def oauth_start(self, state: str, redirect_uri: str) -> str:
        raise AdapterError("腾讯文档当前账号未提供可用的 OAuth 配置；请使用平台连接凭据")

    def verify_credentials(self, access_token: str, open_id: str) -> None:
        """连接前真实调用官方「获取文档列表」接口验证凭据；任何失败抛 AdapterError。"""
        api_base = self._api_base()
        client_id = str(os.getenv("TENCENT_DOC_APP_ID") or "").strip()
        if not client_id:
            raise AdapterError("腾讯文档需要配置 TENCENT_DOC_APP_ID 才能验证凭据")
        headers = {"Accept": "application/json", "Access-Token": access_token, "Client-Id": client_id, "Open-Id": open_id}
        try:
            response = httpx.get(f"{api_base}/openapi/drive/v2/folders", headers=headers, params={"limit": 1}, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            raise AdapterError(f"腾讯文档服务暂不可达：{exc}") from exc
        self._business_data(response)

    def exchange_code(self, code: str, redirect_uri: str) -> PlatformIdentity:
        raise AdapterError("腾讯文档当前账号未提供可用的 OAuth 配置")

    @staticmethod
    def _normalise_encoded_id(resource_id: str, resource_url: str | None) -> str:
        candidate = resource_id.strip()
        if candidate.startswith(("http://", "https://")):
            parsed = urlparse(candidate)
            if parsed.scheme != "https" or parsed.hostname != "docs.qq.com" or parsed.query or parsed.fragment:
                raise AdapterError("腾讯文档资源链接必须是 docs.qq.com 的 HTTPS 地址")
            candidate = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if not candidate and resource_url:
            parsed = urlparse(str(resource_url).strip())
            if parsed.scheme != "https" or parsed.hostname != "docs.qq.com" or parsed.query or parsed.fragment:
                raise AdapterError("腾讯文档资源链接必须是 docs.qq.com 的 HTTPS 地址")
            candidate = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        return candidate.strip()

    @staticmethod
    def _occurred_at(value: Any) -> str | None:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return str(value)

    @staticmethod
    def _business_data(response: httpx.Response) -> dict[str, Any]:
        data = _json(response)
        ret = data.get("ret")
        if not isinstance(ret, int):
            raise AdapterError("腾讯文档 Open API 返回的数据结构不正确")
        if ret != 0:
            message = str(data.get("msg") or "腾讯文档 Open API 返回业务错误")[:200]
            raise AdapterError(f"腾讯文档 API 错误（{ret}）：{message}")
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise AdapterError("腾讯文档 Open API 返回的数据结构不正确")
        return payload

    def fetch_events(self, access_token: str, config: dict[str, Any]) -> list[StandardEvent]:
        api_base = self._api_base()
        client_id = str(os.getenv("TENCENT_DOC_APP_ID") or "").strip()
        open_id = str(config.get("open_id") or config.get("external_account_id") or "").strip()
        resource_type = str(config.get("resource_type") or "document").strip()
        resource_id = self._normalise_encoded_id(
            str(config.get("resource_id") or ""),
            str(config.get("resource_url") or "") or None,
        )
        if resource_type != "document":
            raise AdapterError("腾讯文档当前按官方 Open API 只支持单文档资源；请将资源类型改为单文档")
        if not api_base or not client_id or not open_id or not access_token or not resource_id:
            raise AdapterError("腾讯文档需要配置 client_id、open_id、access_token 与 resource_id")

        headers = {
            "Accept": "application/json",
            "Access-Token": access_token,
            "Client-Id": client_id,
            "Open-Id": open_id,
        }
        if resource_id.startswith("D"):
            converter = httpx.get(
                f"{api_base}/openapi/drive/v2/util/converter",
                headers=headers,
                params={"type": 2, "value": resource_id},
                timeout=HTTP_TIMEOUT,
            )
            converted = self._business_data(converter)
            resource_id = str(converted.get("fileID") or "").strip()
            if not resource_id:
                raise AdapterError("腾讯文档 fileID 转换接口未返回 fileID")

        configured_path = str(config.get("api_path") or "").strip()
        allowed_paths = {
            "documents/detail",
            "metadata",
            "/metadata",
            self.default_metadata_path,
            self.default_metadata_path.lstrip("/"),
        }
        if configured_path and configured_path not in allowed_paths:
            raise AdapterError("腾讯文档当前仅允许官方查询文档元信息接口")
        path = self.default_metadata_path.replace("{fileID}", quote(resource_id, safe="$"))
        response = httpx.get(f"{api_base}/{path.lstrip('/')}", headers=headers, timeout=HTTP_TIMEOUT)
        item = self._business_data(response)
        item_id = str(item.get("ID") or resource_id)
        revision = str(item.get("lastModifyTime") or item.get("lastModifyName") or "unknown")
        occurred_at = self._occurred_at(item.get("lastModifyTime"))
        return [StandardEvent(
            external_id=f"tencent_doc:{item_id}:{revision}",
            event_type="document_updated",
            title=str(item.get("title") or "腾讯文档更新")[:200],
            description="由腾讯文档官方 Open API 查询文档元信息",
            evidence_url=str(item.get("url") or config.get("resource_url") or "") or None,
            occurred_at=occurred_at,
            actor=str(item.get("lastModifyName") or "") or None,
            payload={"resource_id": resource_id, "item": item},
        )]


ADAPTERS: dict[str, PlatformAdapter] = {
    "feishu": FeishuAdapter(),
    "tencent_doc": TencentDocAdapter(),
}