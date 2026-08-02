"""JD 智能搜索服务 — MCP（mcp-jobs）为主，LLM 生成为兜底的双层 fallback 架构。

通过 Python asyncio subprocess 启动 mcp-jobs MCP Server（Node.js），
使用 MCP JSON-RPC over stdio 协议通信，调用 mcp_search_job 获取真实 JD。
当 MCP 不可用时，回退到 LLM 基于训练知识生成 JD。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from config_loader import get_job_search_config, get_job_knowledge_config
from log import get_logger

logger = get_logger("agent")

# ── MCP JSON-RPC 协议常量 ──────────────────────────────────────────
_MCP_PROTOCOL_VERSION = "2024-11-05"
_MCP_CLIENT_NAME = "interview-assistant"
_MCP_CLIENT_VERSION = "1.0.0"
_MCP_METHOD_INITIALIZE = "initialize"
_MCP_METHOD_TOOLS_LIST = "tools/list"
_MCP_METHOD_TOOLS_CALL = "tools/call"
_MCP_METHOD_NOTIFICATIONS_INITIALIZED = "notifications/initialized"


# ── MCP Client（轻量 asyncio subprocess + JSON-RPC）─────────────────

class MCPClient:
    """轻量 MCP JSON-RPC stdio 客户端，不依赖 mcp Python SDK。"""

    def __init__(self, command: str, args: list[str], timeout: float = 30.0):
        self._command = command
        self._args = args
        self._timeout = timeout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._tools: list[dict] = []

    async def connect(self) -> bool:
        """启动 MCP Server 子进程并完成初始化握手。"""
        try:
            self._process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    self._command,
                    *self._args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=10.0,
            )
            # 初始化握手
            init_response = await self._send_request(_MCP_METHOD_INITIALIZE, {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": _MCP_CLIENT_NAME, "version": _MCP_CLIENT_VERSION},
            })
            if init_response is None:
                return False
            # 发送 initialized 通知
            self._send_notification(_MCP_METHOD_NOTIFICATIONS_INITIALIZED, {})
            # 获取工具列表
            tools_response = await self._send_request(_MCP_METHOD_TOOLS_LIST, {})
            if tools_response and "tools" in tools_response:
                self._tools = tools_response["tools"]
                logger.info("MCP Server connected, %d tools available: %s",
                            len(self._tools), [t.get("name") for t in self._tools])
                return True
            return False
        except asyncio.TimeoutError:
            logger.warning("MCP Server connection timed out after 10s")
            return False
        except Exception as exc:
            logger.warning("MCP Server connection failed: %s", exc)
            return False

    async def call_tool(self, tool_name: str, arguments: dict) -> Optional[Any]:
        """调用 MCP 工具并返回解析后的结果。"""
        response = await self._send_request(_MCP_METHOD_TOOLS_CALL, {
            "name": tool_name,
            "arguments": arguments,
        })
        if response is None:
            return None
        # MCP 工具返回格式：{ content: [{ type: "text", text: "..." }] }
        content = response.get("content", [])
        if content and isinstance(content, list):
            # 尝试解析第一个 text 块为 JSON
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return text
        return response

    async def close(self):
        """关闭 MCP Server 子进程。"""
        if self._process and self._process.returncode is None:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            logger.info("MCP Server process closed")

    # ── 内部方法 ────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_notification(self, method: str, params: dict) -> None:
        """发送 JSON-RPC 通知（无需响应）。"""
        if self._process and self._process.stdin:
            notification = {"jsonrpc": "2.0", "method": method, "params": params}
            raw = json.dumps(notification, ensure_ascii=False) + "\n"
            try:
                self._process.stdin.write(raw.encode("utf-8"))
            except Exception:
                pass

    async def _send_request(self, method: str, params: dict) -> Optional[dict]:
        """发送 JSON-RPC 请求并等待响应。"""
        if self._process is None or self._process.stdout is None:
            return None
        request_id = self._next_id()
        request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        raw = json.dumps(request, ensure_ascii=False) + "\n"
        try:
            self._process.stdin.write(raw.encode("utf-8"))
            await self._process.stdin.drain()
        except Exception as exc:
            logger.warning("MCP write failed: %s", exc)
            return None
        try:
            line_bytes = await asyncio.wait_for(
                self._process.stdout.readline(), timeout=self._timeout
            )
            if not line_bytes:
                return None
            response = json.loads(line_bytes.decode("utf-8"))
            if "error" in response:
                logger.warning("MCP RPC error for method '%s': %s", method, response["error"])
                return None
            return response.get("result")
        except asyncio.TimeoutError:
            logger.warning("MCP request '%s' timed out after %.1fs", method, self._timeout)
            return None
        except Exception as exc:
            logger.warning("MCP request '%s' failed: %s", method, exc)
            return None


# ── JD 搜索服务 ────────────────────────────────────────────────────

class JobSearchService:
    """JD 智能搜索服务：MCP 实时搜索 → LLM 知识生成 双层 fallback。"""

    MCP_TIMEOUT = 30.0  # MCP 单次搜索超时（秒）

    # ── 第 1 层：MCP 搜索 ──────────────────────────────────────

    @staticmethod
    async def search_via_mcp(job_title: str, city: str = "") -> Optional[list[dict]]:
        """
        通过 MCP mcp-jobs Server 搜索真实 JD 列表。

        成功返回职位 dict 列表，失败（超时/无结果/进程异常）返回 None。
        """
        config = get_job_search_config()
        if not config.get("mcp_enabled", True):
            logger.info("MCP job search disabled by config")
            return None

        mcp_command = config.get("mcp_server_command", "npx")
        mcp_args = config.get("mcp_server_args", ["-y", "mcp-jobs"])
        timeout = float(config.get("mcp_timeout", JobSearchService.MCP_TIMEOUT))

        client = MCPClient(mcp_command, list(mcp_args), timeout=timeout)
        try:
            # 1. 连接 MCP Server
            connected = await client.connect()
            if not connected:
                logger.warning("MCP Server failed to connect, falling back to LLM")
                return None

            # 2. 调用 mcp_search_job
            search_args: dict = {"title": job_title, "limit": 5}
            if city:
                search_args["city"] = city
            if config.get("default_city") and not city:
                search_args["city"] = config["default_city"]

            raw_result = await client.call_tool("mcp_search_job", search_args)
            if raw_result is None:
                logger.warning("mcp_search_job returned no results")
                return None

            # 3. 标准化结果
            jobs = JobSearchService._normalize_mcp_results(raw_result, job_title)
            if not jobs:
                logger.warning("mcp_search_job results were empty after normalization")
                return None

            logger.info("MCP search found %d jobs for '%s'", len(jobs), job_title)
            return jobs

        except Exception as exc:
            logger.warning("MCP search failed with exception: %s", exc)
            return None
        finally:
            await client.close()

    @staticmethod
    def _normalize_mcp_results(raw: Any, job_title: str) -> list[dict]:
        """将 mcp-jobs 返回的异构结果标准化为统一格式。"""
        jobs: list[dict] = []
        items = raw if isinstance(raw, list) else [raw]

        for item in items:
            if not isinstance(item, dict):
                continue
            jobs.append({
                "job_id": item.get("job_id", item.get("id", str(uuid.uuid4().hex[:12]))),
                "title": item.get("title", item.get("job_name", job_title)),
                "company": item.get("company", item.get("company_name", "")),
                "city": item.get("city", item.get("location", item.get("work_city", ""))),
                "salary": item.get("salary", item.get("salary_range", "")),
                "experience": item.get("experience", item.get("experience_requirement", "")),
                "education": item.get("education", item.get("education_requirement", "")),
                "description": item.get("description", item.get("jd", item.get("detail", ""))),
                "tech_stack": item.get("tech_stack", item.get("tags", [])),
                "platform": item.get("platform", item.get("source", "mcp-jobs")),
                "url": item.get("url", item.get("link", "")),
                "raw": item,  # 保留原始数据
            })
        return jobs

    # ── 第 2 层：LLM 生成（fallback）───────────────────────────

    @staticmethod
    async def search_via_llm(job_title: str) -> Optional[dict]:
        """
        LLM 基于训练知识生成标准 JD，作为 MCP 不可用时的兜底方案。
        返回与 JD Agent 兼容的 dict。
        """
        try:
            from models.llm import get_llm, ainvoke_json_with_schema
            from agents.json_contracts import JDAnalysisOutput
            from prompts.jd_search import JD_SEARCH_PROMPT

            prompt = JD_SEARCH_PROMPT.format(job_title=job_title)
            llm = get_llm()
            result: JDAnalysisOutput = await ainvoke_json_with_schema(
                llm, prompt, JDAnalysisOutput, logger, "JD Search (LLM fallback)"
            )
            logger.info("LLM fallback generated JD for '%s'", job_title)
            return {
                "job_id": f"llm_{uuid.uuid4().hex[:12]}",
                "title": result.title or job_title,
                "company": "",
                "city": "",
                "salary": "",
                "experience": result.experience_requirement or "",
                "education": result.education_requirement or "",
                "description": "",  # LLM 生成的 JD 无原始文本
                "industry": result.industry or "",
                "tech_stack": result.tech_stack or [],
                "keywords": result.keywords or [],
                "hard_skills": result.hard_skills or [],
                "soft_skills": result.soft_skills or [],
                "responsibilities": result.responsibilities or [],
                "implicit_preferences": result.implicit_preferences or [],
                "bonus_items": result.bonus_items or [],
                "platform": "llm-generated",
                "is_llm_fallback": True,
            }
        except Exception as exc:
            logger.error("LLM fallback also failed for '%s': %s", job_title, exc)
            return None

    # ── 第 2 层：岗位知识库检索 ────────────────────────────────

    @staticmethod
    async def search_via_knowledge_base(job_title: str, city: str = "") -> Optional[list[dict]]:
        """从岗位知识库中检索匹配的职位。

        先查 ChromaDB job_knowledge 集合做语义搜索，
        如果结果为空，尝试将长查询拆解为短关键词二次检索。

        Args:
            job_title: 用户搜索的岗位名称
            city: 城市（可选，用于过滤）

        Returns:
            list[dict] 与 _normalize_mcp_results 兼容的格式，或 None
        """
        from services.job_knowledge import JobKnowledgeBase

        config = get_job_knowledge_config()
        if not config.get("enabled", True):
            return None

        filters = None
        if city:
            filters = {"city": city}

        try:
            # 第一次检索：完整查询
            results = await JobKnowledgeBase.search_jobs(
                query=job_title,
                filters=filters,
            )

            # 如果结果为空，尝试拆解关键词二次检索
            if not results and len(job_title) > 6:
                shorter_queries = _split_job_query(job_title)
                for sub_query in shorter_queries:
                    sub_results = await JobKnowledgeBase.search_jobs(
                        query=sub_query,
                        filters=filters,
                    )
                    if sub_results:
                        results = sub_results
                        break

            if results:
                logger.info(
                    "Knowledge base found %d jobs for '%s' (city='%s')",
                    len(results), job_title, city,
                )
                return results

            logger.info(
                "Knowledge base returned no results for '%s' (city='%s')",
                job_title, city,
            )
            return None

        except ImportError:
            logger.warning("job_knowledge module not available")
            return None
        except Exception as exc:
            logger.warning("Knowledge base search failed for '%s': %s", job_title, exc)
            return None

    # ── 统一入口 ──────────────────────────────────────────────

    @staticmethod
    async def search_jd(
        job_title: str,
        city: str = "",
        force_llm: bool = False,
    ) -> dict:
        """
        JD 搜索统一入口（三层 fallback）。

        第 1 层：岗位知识库（ChromaDB job_knowledge 集合，离线预采集）
        第 2 层：LLM 生成（基于训练知识）
        第 3 层：提示手动输入

        返回格式：
        {
            "source": "job_kb" | "llm" | "failed",
            "query": "原始查询",
            "jobs": [{...}, ...],
            "count": int,
            "message": "给用户的提示"
        }
        """
        logger.info("JD Search started for '%s' (city='%s', force_llm=%s)", job_title, city, force_llm)

        jobs: list[dict] = []

        # 第 1 层：岗位知识库检索
        if not force_llm:
            kb_results = await JobSearchService.search_via_knowledge_base(job_title, city)
            if kb_results:
                jobs = kb_results
                return {
                    "source": "job_kb",
                    "query": job_title,
                    "jobs": jobs,
                    "count": len(jobs),
                    "message": f"从岗位知识库中找到 {len(jobs)} 个相关职位，请选择最匹配的一个。",
                }

        # 第 2 层：LLM 生成
        llm_fallback = get_job_search_config().get("fallback_to_llm", True)
        if llm_fallback or force_llm:
            llm_result = await JobSearchService.search_via_llm(job_title)
            if llm_result:
                jobs = [llm_result]
                return {
                    "source": "llm",
                    "query": job_title,
                    "jobs": jobs,
                    "count": 1,
                    "message": (
                        f"未在岗位知识库中找到匹配数据，已根据「{job_title}」的行业通用要求生成了 JD。"
                        f"你可以在此基础上手动调整或补充细节。"
                    ),
                }

        # 全部失败
        return {
            "source": "failed",
            "query": job_title,
            "jobs": [],
            "count": 0,
            "message": f"很抱歉，未能获取到「{job_title}」的职位信息。请尝试使用更具体的职位名称，或直接粘贴 JD 内容。",
        }

    @staticmethod
    def build_job_from_search_result(selected_job: dict, session_id: str) -> dict:
        """
        将搜索结果中的单个职位转换为 JD Agent 可用的 Job 对象 dict。
        用于在用户选择某个 JD 后，直接写入 state.job。

        对于 LLM 生成的 JD，会根据结构化字段组合一份 human-readable 的 JD 文本
        作为 source，弥补没有原文的问题。
        """
        from workflow.state import Job

        now = datetime.now(timezone.utc).isoformat()
        platform = selected_job.get("platform", "unknown")

        # 如果是 LLM 生成的，拼一份可读 JD 描述作为 source
        if selected_job.get("is_llm_fallback"):
            source_text = JobSearchService._compose_jd_text(selected_job)
        else:
            source_text = selected_job.get("description", "") or f"platform://{platform}"

        job = Job(
            id=f"job_{selected_job.get('job_id', uuid.uuid4().hex[:12])}",
            source=source_text,
            parsed_at=now,
            version=1,
            industry=selected_job.get("industry", ""),
            title=selected_job.get("title", ""),
            tech_stack=selected_job.get("tech_stack", []),
            keywords=selected_job.get("keywords", []),
            hard_skills=selected_job.get("hard_skills", []),
            soft_skills=selected_job.get("soft_skills", []),
            responsibilities=selected_job.get("responsibilities", []),
            education_requirement=selected_job.get(
                "education", selected_job.get("education_requirement", "")
            ),
            experience_requirement=selected_job.get(
                "experience", selected_job.get("experience_requirement", "")
            ),
            implicit_preferences=selected_job.get("implicit_preferences", []),
            bonus_items=selected_job.get("bonus_items", []),
        )
        return job.model_dump()

    @staticmethod
    def _compose_jd_text(job: dict) -> str:
        """将 LLM 生成的结构化 JD 字段拼成可读文本，供 source 字段使用。"""
        lines = []
        title = job.get("title", "")
        if title:
            lines.append(f"【{title}】")
        industry = job.get("industry", "")
        if industry:
            lines.append(f"行业：{industry}")

        tech = job.get("tech_stack", [])
        if tech:
            lines.append(f"技术栈：{', '.join(tech)}")
        hard = job.get("hard_skills", [])
        if hard:
            lines.append(f"硬技能要求：{', '.join(hard)}")
        soft = job.get("soft_skills", [])
        if soft:
            lines.append(f"软技能要求：{', '.join(soft)}")

        edu = job.get("education_requirement", "") or job.get("education", "")
        if edu:
            lines.append(f"学历要求：{edu}")
        exp = job.get("experience_requirement", "") or job.get("experience", "")
        if exp:
            lines.append(f"经验要求：{exp}")

        resp = job.get("responsibilities", [])
        if resp:
            lines.append("岗位职责：")
            for r in resp:
                lines.append(f"  - {r}")

        bonus = job.get("bonus_items", [])
        if bonus:
            lines.append(f"加分项：{', '.join(bonus)}")

        return "\n".join(lines)


# ── 辅助函数 ──────────────────────────────────────────────


def _split_job_query(query: str) -> list[str]:
    """将长岗位名称拆解为短关键词列表，用于知识库二次检索。

    例如 "AI Agent应用开发工程师 大模型" → ["AI Agent", "开发工程师"]
    策略：去掉通用后缀词，按空格/关键词拆分取最短有意义的片段。
    """
    import re

    if not query or len(query) <= 6:
        return []

    # 去掉城市后缀（"AI工程师 北京" → "AI工程师"）
    short = re.sub(r"\s+(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|重庆)$", "", query.strip())

    # 按空格拆分
    parts = [p.strip() for p in re.split(r"[\s,，/、]+", short) if p.strip()]

    # 如果拆分后只有一段，尝试去掉通用后缀
    if len(parts) <= 1:
        stripped = re.sub(r"(工程师|开发|设计|运营|经理|专员|实习生|管培生)$", "", short)
        if stripped and stripped != short:
            return [stripped.strip()]
        return []

    # 取最长的一段（通常是核心关键词）
    results = sorted(parts, key=len, reverse=True)[:2]
    return results
