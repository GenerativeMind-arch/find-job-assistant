"""interview-boss CLI — Interview Assistant 的 BOSS直聘命令行工具。

命令返回结构化 JSON 到 stdout，Agent 友好。后端地址通过 INTERVIEW_BOSS_API 覆盖（默认 http://127.0.0.1:8000）。

用法:
    python -m cli.cli status
    python -m cli.cli search "Python" --city 广州
    python -m cli.cli scan-apply
    python -m cli.cli agent-run "搜索广州Python岗位，只搜不投"
    python -m cli.cli import-job <job_url>
"""

import json
import os
import sys
import click

from . import client, output


@click.group()
def main():
    """interview-boss — Interview Assistant BOSS直聘命令行工具"""


# ── 版本 ──
@main.command("version")
def version_cmd():
    output.emit(output.ok("version", data={"version": "2.0.0", "name": "interview-boss"}))


# ── Schema：Agent 工具描述 ──
@main.command("schema")
def schema_cmd():
    path = os.path.join(os.path.dirname(__file__), "schema.json")
    if not os.path.exists(path):
        # 从后端实时获取工具 schema
        resp = client.agent_tools()
        if resp.is_error:
            output.emit(output.fail("schema", "schema.json 不存在且后端不可用"))
            return
        output.emit(output.ok("schema", data=resp.json()))
        return
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    output.emit(output.ok("schema", data=schema))


# ── 系统状态 ──
@main.command("status")
def status_cmd():
    resp = client.status()
    output.emit(output.ok_or_fail(resp, "status"))


@main.command("start")
def start_cmd():
    """启动 BOSS 浏览器实例（首次需扫码登录）。"""
    resp = client.start()
    output.emit(output.ok_or_fail(resp, "start"))


@main.command("stop")
def stop_cmd():
    """停止 BOSS 浏览器实例。"""
    resp = client.stop()
    output.emit(output.ok_or_fail(resp, "stop"))


@main.command("login")
def login_cmd():
    """重新扫码登录 BOSS直聘。"""
    resp = client.relogin()
    output.emit(output.ok_or_fail(resp, "login"))


@main.command("heartbeat")
def heartbeat_cmd():
    """心跳保活检查。"""
    resp = client.heartbeat()
    output.emit(output.ok_or_fail(resp, "heartbeat"))


@main.command("monitor-pause")
def monitor_pause_cmd():
    """暂停自动回复监控。"""
    resp = client.monitor_pause()
    output.emit(output.ok_or_fail(resp, "monitor-pause"))


@main.command("monitor-resume")
def monitor_resume_cmd():
    """恢复自动回复监控。"""
    resp = client.monitor_resume()
    output.emit(output.ok_or_fail(resp, "monitor-resume"))


# ── 搜索 ──
@main.command("search")
@click.argument("keyword")
@click.option("--city", default="", help="城市名（空则使用设置中的默认城市）")
@click.option("--welfare", default=None, help="福利筛选 如 双休,五险一金")
@click.option("--count", type=int, default=60, help="返回条数上限")
def search_cmd(keyword, city, welfare, count):
    """搜索BOSS直聘岗位。"""
    resp = client.search(keyword, city, welfare or "", count)
    output.emit(output.ok_or_fail(resp, "search"))


@main.command("jobs")
@click.option("--status", "filter_status", default=None, help="pending / applied / replied")
@click.option("--limit", type=int, default=100)
def jobs_cmd(filter_status, limit):
    """列出数据库中的岗位。"""
    resp = client.jobs(filter_status, limit)
    output.emit(output.ok_or_fail(resp, "jobs"))


@main.command("scan")
def scan_cmd():
    """扫描当前BOSS搜索结果页，提取所有可见岗位。"""
    resp = client.scan()
    output.emit(output.ok_or_fail(resp, "scan"))


# ── 投递 ──
@main.command("apply")
@click.argument("job_url")
def apply_cmd(job_url):
    """单岗投递（含 AI 招呼语）。"""
    resp = client.apply_one(job_url)
    output.emit(output.ok_or_fail(resp, "apply"))


@main.command("apply-batch")
@click.option("--status", "filter_status", default="pending", help="批量投递指定状态的岗位")
def apply_batch_cmd(filter_status):
    r = client.jobs(filter_status, limit=200)
    if r.is_error:
        output.emit(output.fail("apply-batch", f"fetch jobs failed: {r.status_code}"))
        return
    jobs_list = r.json().get("jobs", [])
    urls = [j["job_url"] for j in jobs_list if j.get("job_url")]
    if not urls:
        output.emit(output.fail("apply-batch", "no job_urls found"))
        return
    resp = client.apply_batch(urls)
    output.emit(output.ok_or_fail(resp, "apply-batch"))


@main.command("scan-apply")
def scan_apply_cmd():
    """扫描当前页面全部岗位并一键批量投递。"""
    resp = client.scan_and_apply()
    output.emit(output.ok_or_fail(resp, "scan-apply"))


# ── 会话 & 沟通 ──
@main.command("conversations")
def conversations_cmd():
    resp = client.conversations()
    output.emit(output.ok_or_fail(resp, "conversations"))


@main.command("chat")
@click.argument("conv_id", type=int)
def chat_cmd(conv_id):
    resp = client.chat_messages(conv_id)
    output.emit(output.ok_or_fail(resp, "chat"))


@main.command("send")
@click.argument("conv_id", type=int)
@click.option("--msg", required=True, help="消息内容")
def send_cmd(conv_id, msg):
    resp = client.send_message(conv_id, msg)
    output.emit(output.ok_or_fail(resp, "send"))


# ── AI 分析 ──
@main.command("analyze")
@click.argument("job_url")
@click.option("--title", default="", help="岗位名称")
@click.option("--company", default="", help="公司名")
@click.option("--desc", default="", help="JD描述")
def analyze_cmd(job_url, title, company, desc):
    """AI 分析岗位 JD 匹配度。"""
    resp = client.analyze(job_url, title, company, desc)
    output.emit(output.ok_or_fail(resp, "analyze"))


# ── 统计 & 设置 ──
@main.command("stats")
def stats_cmd():
    """投递转化漏斗统计。"""
    resp = client.stats()
    output.emit(output.ok_or_fail(resp, "stats"))


@main.command("settings")
def settings_cmd():
    """查看全部设置（API Key 脱敏）。"""
    resp = client.get_settings()
    output.emit(output.ok_or_fail(resp, "settings"))


@main.command("settings-set")
@click.argument("key")
@click.argument("value")
def settings_set_cmd(key, value):
    """更新单个设置项。"""
    resp = client.update_settings({key: value})
    output.emit(output.ok_or_fail(resp, "settings-set"))


# ── 候选池 ──
@main.command("shortlist")
@click.argument("action", type=click.Choice(["list", "add", "remove"]))
@click.option("--job-url", help="岗位URL")
@click.option("--title", default="", help="岗位名称")
@click.option("--company", default="", help="公司名")
@click.option("--id", "sid", type=int, help="shortlist ID")
def shortlist_cmd(action, job_url, title, company, sid):
    """候选池管理。"""
    if action == "list":
        resp = client.get_shortlists()
        output.emit(output.ok_or_fail(resp, "shortlist"))
    elif action == "add":
        if not job_url:
            output.emit(output.fail("shortlist", "--job-url required"))
            return
        resp = client.add_shortlist(job_url, title, company)
        output.emit(output.ok_or_fail(resp, "shortlist"))
    elif action == "remove":
        if not sid:
            output.emit(output.fail("shortlist", "--id required"))
            return
        resp = client.remove_shortlist(sid)
        output.emit(output.ok_or_fail(resp, "shortlist"))


# ── Agent Harness（可选高级功能）──
@main.command("agent-tools")
def agent_tools_cmd():
    """列出 Agent 可用的 17 个工具。"""
    resp = client.agent_tools()
    output.emit(output.ok_or_fail(resp, "agent-tools"))


@main.command("agent-run")
@click.argument("goal")
@click.option("--max-steps", type=int, default=12, help="最大执行步数")
def agent_run_cmd(goal, max_steps):
    """运行 Plan-then-Execute Agent 自主执行求职任务（自动投递前请确认"不投递"约束）。"""
    resp = client.agent_run(goal, max_steps)
    output.emit(output.ok_or_fail(resp, "agent-run"))


# ── 工作流桥接 ──
@main.command("import-job")
@click.argument("job_url")
@click.option("--session", "session_id", default="", help="目标会话ID（留空则新建）")
def import_job_cmd(job_url, session_id):
    """将 Boss 岗位导入主工作流（JD→差距→简历→面试流水线）。"""
    resp = client.import_job(job_url=job_url, session_id=session_id)
    output.emit(output.ok_or_fail(resp, "import-job"))


if __name__ == "__main__":
    main()
