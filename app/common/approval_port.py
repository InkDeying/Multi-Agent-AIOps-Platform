"""Runtime 使用的审批端口。

这里只定义最小协议和进程级注册点，实际实现由组合根注册数据库仓储。
"""

from typing import Any, Protocol


class ApprovalPort(Protocol):
    async def create_request(self, **kwargs: Any) -> str: ...

    async def wait_for_decision(self, req_id: str, **kwargs: Any) -> str: ...


_approval_port: ApprovalPort | None = None


def set_approval_port(port: ApprovalPort) -> None:
    global _approval_port
    _approval_port = port


def get_approval_port() -> ApprovalPort | None:
    return _approval_port
