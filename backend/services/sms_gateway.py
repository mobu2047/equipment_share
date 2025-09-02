"""
短信网关占位（开发模式）

当前：
- 不实际发送短信；
- 校验时若为开发模式接受固定验证码；
- 提供接口以便未来接入真实第三方短信供应商。
"""

from __future__ import annotations

from backend.core.config import get_settings


class SMSGateway:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def send_code(self, *, phone: str, scene: str) -> dict:
        """发送验证码（开发模式仅回显）。"""
        if self.settings.sms_dev_mode:
            return {"dev": True, "code": self.settings.sms_dev_code}
        # 预留接入第三方网关的实现
        return {"sent": True}

    async def verify_code(self, *, phone: str, code: str, scene: str) -> bool:
        """验证验证码（开发模式接受固定码）。"""
        if self.settings.sms_dev_mode:
            return code == self.settings.sms_dev_code
        # 真实网关模式：应比对 sms_codes 表或供应商校验结果
        return False


