"""告警 / 事件组 / 诊断任务的 Postgres Repository.

这个类曾经是一个 832 行的 god repository: 告警归一化策略、两条建单路径、
任务状态流转、跨四张外部表的级联删除全挤在一个文件里。现在按职责分开,
类本身只做组装 —— 对外暴露的 ``incident_repository`` 接口一个方法都没变:

  - ``normalizer.py``   告警归一化与关联口径 (纯函数, 不碰数据库)
  - ``rows.py``         这几张表的 JSONB 行解码
  - ``ingest.py``       Alertmanager 入库 + 手动升级建单
  - ``task_store.py``   diagnosis_tasks 的生命周期读写
  - ``task_purge.py``   终态任务的删除与跨域级联清理

用 mixin 组装而不是拆成三个 Repository 对象, 是为了让 29 处调用点
(``incident_repository.get_task`` 等) 完全不用动。
"""

from __future__ import annotations

from app.incidents.ingest import IngestMixin
from app.incidents.task_purge import TaskPurgeMixin
from app.incidents.task_store import TaskStoreMixin


class IncidentRepository(IngestMixin, TaskStoreMixin, TaskPurgeMixin):
    """事件事实链、诊断任务与终态清理的统一 Repository 门面."""


incident_repository = IncidentRepository()
