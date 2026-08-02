#!/usr/bin/env python3
"""Interview Assistant CLI — 命令行工具入口。

支持两类命令:
  1. 面试助手核心命令 (chat, resume, export, ...)
  2. BOSS 直聘自动化命令 (boss start/stop/search/apply/...)

所有命令输出统一 JSON 信封格式，对 AI Agent 友好。
"""

import argparse
import json
import sys

from . import client
from .output import ok, fail, emit, ok_or_fail


def _resp_to_emit(resp, command: str):
    """将 HTTP 响应转换为 JSON 信封并输出。"""
    emit(ok_or_fail(resp, command))


# ══════════════════════════════════════
#  通用命令
# ══════════════════════════════════════

def cmd_health(args):
    resp = client.health()
    _resp_to_emit(resp, "health")


def cmd_chat(args):
    resp = client.chat(args.message, args.session or "")
    _resp_to_emit(resp, "chat")


def cmd_search_jobs(args):
    resp = client.search_jobs(args.keyword, args.city or "", args.limit)
    _resp_to_emit(resp, "search_jobs")


# ══════════════════════════════════════
#  BOSS 直聘自动化命令
# ══════════════════════════════════════

def cmd_boss_health(args):
    resp = client.boss_health()
    _resp_to_emit(resp, "boss_health")


def cmd_boss_status(args):
    resp = client.boss_status()
    _resp_to_emit(resp, "boss_status")


def cmd_boss_start(args):
    print("正在启动浏览器...", file=sys.stderr)
    resp = client.boss_start()
    _resp_to_emit(resp, "boss_start")


def cmd_boss_stop(args):
    resp = client.boss_stop()
    _resp_to_emit(resp, "boss_stop")


def cmd_boss_relogin(args):
    print("请在弹出的浏览器窗口中扫码登录...", file=sys.stderr)
    resp = client.boss_relogin()
    _resp_to_emit(resp, "boss_relogin")


def cmd_boss_search(args):
    print(f"正在搜索: {args.keyword} (城市: {args.city or '全国'})...", file=sys.stderr)
    resp = client.boss_search_jobs(args.keyword, args.city or "", args.welfare or "", args.limit)
    _resp_to_emit(resp, "boss_search")


def cmd_boss_jobs(args):
    resp = client.boss_list_jobs(args.status or "", args.limit)
    _resp_to_emit(resp, "boss_jobs")


def cmd_boss_apply(args):
    print(f"正在投递: {args.job_url}...", file=sys.stderr)
    resp = client.boss_apply(args.job_url, args.greeting or "")
    _resp_to_emit(resp, "boss_apply")


def cmd_boss_apply_batch(args):
    urls = args.job_urls
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]
    print(f"正在批量投递 {len(urls)} 个岗位...", file=sys.stderr)
    resp = client.boss_apply_batch(urls, args.greeting or "")
    _resp_to_emit(resp, "boss_apply_batch")


def cmd_boss_scan_apply(args):
    print("正在扫描当前页面并批量投递...", file=sys.stderr)
    resp = client.boss_scan_and_apply(args.greeting or "")
    _resp_to_emit(resp, "boss_scan_apply")


def cmd_boss_scan(args):
    print("正在扫描当前页面...", file=sys.stderr)
    resp = client.boss_scan_page()
    _resp_to_emit(resp, "boss_scan")


def cmd_boss_analyze(args):
    print(f"正在分析岗位: {args.job_url}...", file=sys.stderr)
    resp = client.boss_analyze(args.job_url, args.title or "", args.company or "", args.desc or "")
    _resp_to_emit(resp, "boss_analyze")


def cmd_boss_conversations(args):
    resp = client.boss_conversations()
    _resp_to_emit(resp, "boss_conversations")


def cmd_boss_messages(args):
    resp = client.boss_conversation_messages(args.conv_id, args.limit)
    _resp_to_emit(resp, "boss_messages")


def cmd_boss_send(args):
    resp = client.boss_send_message(args.conv_id, args.content)
    _resp_to_emit(resp, "boss_send")


def cmd_boss_pause(args):
    resp = client.boss_pause_monitor()
    _resp_to_emit(resp, "boss_pause")


def cmd_boss_resume(args):
    resp = client.boss_resume_monitor()
    _resp_to_emit(resp, "boss_resume")


def cmd_boss_stats(args):
    resp = client.boss_stats()
    _resp_to_emit(resp, "boss_stats")


def cmd_boss_settings(args):
    if args.set:
        # --set key=value key2=value2
        updates = {}
        for kv in args.set:
            if "=" in kv:
                k, v = kv.split("=", 1)
                updates[k] = v
        if updates:
            resp = client.boss_update_settings(**updates)
            _resp_to_emit(resp, "boss_settings_update")
            return
    resp = client.boss_get_settings()
    _resp_to_emit(resp, "boss_settings")


# ══════════════════════════════════════
#  参数解析
# ══════════════════════════════════════

def build_parser():
    parser = argparse.ArgumentParser(
        prog="interview",
        description="Interview Assistant CLI - 多 Agent 面试助手 + BOSS 直聘自动化",
    )
    sub = parser.add_subparsers(dest="command")

    # health
    p = sub.add_parser("health", help="健康检查")
    p.set_defaults(func=cmd_health)

    # chat
    p = sub.add_parser("chat", help="发送消息")
    p.add_argument("message", help="消息内容")
    p.add_argument("--session", "-s", default="", help="会话 ID")
    p.set_defaults(func=cmd_chat)

    # search
    p = sub.add_parser("search", help="搜索岗位（知识库）")
    p.add_argument("keyword", help="搜索关键词")
    p.add_argument("--city", "-c", default="")
    p.add_argument("--limit", "-l", type=int, default=10)
    p.set_defaults(func=cmd_search_jobs)

    # ── BOSS 直聘自动化 ──
    boss = sub.add_parser("boss", help="BOSS 直聘自动化")
    boss_sub = boss.add_subparsers(dest="boss_command")

    p = boss_sub.add_parser("health", help="Boss 模块健康检查")
    p.set_defaults(func=cmd_boss_health)

    p = boss_sub.add_parser("status", help="Boss 自动化状态")
    p.set_defaults(func=cmd_boss_status)

    p = boss_sub.add_parser("start", help="启动浏览器自动化")
    p.set_defaults(func=cmd_boss_start)

    p = boss_sub.add_parser("stop", help="停止浏览器自动化")
    p.set_defaults(func=cmd_boss_stop)

    p = boss_sub.add_parser("relogin", help="重新扫码登录")
    p.set_defaults(func=cmd_boss_relogin)

    p = boss_sub.add_parser("search", help="搜索 BOSS 直聘岗位")
    p.add_argument("keyword", help="搜索关键词")
    p.add_argument("--city", "-c", default="", help="城市名")
    p.add_argument("--welfare", "-w", default="", help="福利筛选（逗号分隔）")
    p.add_argument("--limit", "-l", type=int, default=60)
    p.set_defaults(func=cmd_boss_search)

    p = boss_sub.add_parser("jobs", help="列出已保存的岗位")
    p.add_argument("--status", "-s", default="")
    p.add_argument("--limit", "-l", type=int, default=100)
    p.set_defaults(func=cmd_boss_jobs)

    p = boss_sub.add_parser("apply", help="投递单个岗位")
    p.add_argument("job_url", help="岗位 URL")
    p.add_argument("--greeting", "-g", default="", help="自定义招呼语")
    p.set_defaults(func=cmd_boss_apply)

    p = boss_sub.add_parser("apply-batch", help="批量投递")
    p.add_argument("job_urls", nargs="*", help="岗位 URL 列表")
    p.add_argument("--from-file", "-f", help="从文件读取 URL（每行一个）")
    p.add_argument("--greeting", "-g", default="")
    p.set_defaults(func=cmd_boss_apply_batch)

    p = boss_sub.add_parser("scan", help="扫描当前页面岗位")
    p.set_defaults(func=cmd_boss_scan)

    p = boss_sub.add_parser("scan-apply", help="扫描当前页并批量投递")
    p.add_argument("--greeting", "-g", default="")
    p.set_defaults(func=cmd_boss_scan_apply)

    p = boss_sub.add_parser("analyze", help="AI 分析岗位匹配度")
    p.add_argument("job_url")
    p.add_argument("--title", "-t", default="")
    p.add_argument("--company", default="")
    p.add_argument("--desc", "-d", default="")
    p.set_defaults(func=cmd_boss_analyze)

    p = boss_sub.add_parser("conversations", help="列出活跃会话")
    p.set_defaults(func=cmd_boss_conversations)

    p = boss_sub.add_parser("messages", help="获取会话消息")
    p.add_argument("conv_id", type=int)
    p.add_argument("--limit", "-l", type=int, default=50)
    p.set_defaults(func=cmd_boss_messages)

    p = boss_sub.add_parser("send", help="手动发送消息")
    p.add_argument("conv_id", type=int)
    p.add_argument("content", help="消息内容")
    p.set_defaults(func=cmd_boss_send)

    p = boss_sub.add_parser("pause", help="暂停自动回复")
    p.set_defaults(func=cmd_boss_pause)

    p = boss_sub.add_parser("resume", help="恢复自动回复")
    p.set_defaults(func=cmd_boss_resume)

    p = boss_sub.add_parser("stats", help="获取统计数据")
    p.set_defaults(func=cmd_boss_stats)

    p = boss_sub.add_parser("settings", help="查看/修改设置")
    p.add_argument("--set", "-s", nargs="*", help="设置键值对 (key=value)")
    p.set_defaults(func=cmd_boss_settings)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
