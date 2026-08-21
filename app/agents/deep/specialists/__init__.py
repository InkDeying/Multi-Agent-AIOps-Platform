"""Deep Diagnosis 的专业 Agent 层.

  - ``spec.py``      一个专业 Agent 的声明式规格 (差异点)
  - ``registry.py``  四个 Agent 的规格 + 职责边界说明
  - ``tools.py``     各自的工具装载 (全部延迟导入)
  - ``runner.py``    共享执行体 (scoped LLM 循环 + Evidence 压制 + 失败降级)
"""

from app.agents.deep.specialists.registry import SPECIALIST_SPECS, get_spec
from app.agents.deep.specialists.runner import run_specialist
from app.agents.deep.specialists.spec import SpecialistSpec

__all__ = ["SPECIALIST_SPECS", "SpecialistSpec", "get_spec", "run_specialist"]
