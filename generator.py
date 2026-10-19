#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Antigravity Usage Dashboard Generator (V6.0 项目层级透视与会话下钻版)
1. 步骤数 (Steps)：Agent 自主执行的原子动作总数 (思考 + 读写文件 + 执行命令 + 工具返回)；
2. 对话轮数 (User Turns)：用户实际提问和交互的次数 (一问一答的轮数)；
3. 新增 Token (Billed Tokens)：净消耗配额的 Token 数；
4. 按项目聚合透视 (Project Workspace Perspective)：
   - 从底层真实 workspace_uris 精准识别所属工作区/项目名与物理路径；
   - 支持【全量明细】与【按项目聚合】双视角无缝切换；
   - 每个项目下均可手风琴展开查阅【项目下全部具体会话】，并点击唤醒侧边逐轮流水抽屉！
5. 保持纯净真实：绝不造假任何模型字段，数据 100% 取自底层真实日志；
6. 保持时间快捷胶囊、7日趋势、深浅双模式、文件模式一键唤醒。
"""

import os
import sys
import glob
import json
import re
import sqlite3
import argparse
import urllib.parse
from datetime import datetime, timedelta
from collections import defaultdict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOTS_DIR = os.path.join(CURRENT_DIR, "snapshots")

def get_snapshot_slug(timestamp_str):
    return timestamp_str.replace(":", "-").replace(" ", "_")

def save_snapshot(summary, conversations):
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    gen_at = summary.get("generated_at", "")
    if not gen_at:
        gen_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary["generated_at"] = gen_at
    slug = get_snapshot_slug(gen_at)
    file_name = f"snapshot_{slug}.json"
    file_path = os.path.join(SNAPSHOTS_DIR, file_name)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "conversations": conversations}, f, ensure_ascii=False, indent=2)
    return file_name

def list_snapshots():
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    files = [f for f in os.listdir(SNAPSHOTS_DIR) if f.startswith("snapshot_") and f.endswith(".json")]
    files.sort(reverse=True)
    result = []
    for f in files:
        raw_slug = f[len("snapshot_"):-len(".json")]
        parts = raw_slug.split("_")
        if len(parts) == 2:
            time_str = parts[0] + " " + parts[1].replace("-", ":")
        else:
            time_str = raw_slug
        result.append({
            "id": f,
            "time": time_str
        })
    return result

def estimate_tokens_from_text(text):
    if not text:
        return 0
    zh_count = 0
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            zh_count += 1
    other_count = len(text) - zh_count
    return int(zh_count * 1.6 + other_count * 0.28)

def detect_antigravity_dir(custom_path=None):
    candidates = []
    if custom_path:
        candidates.append(os.path.abspath(custom_path))
    
    env_dir = os.environ.get("ANTIGRAVITY_DATA") or os.environ.get("ANTIGRAVITY_HOME")
    if env_dir:
        candidates.append(env_dir)

    default_user_dir = os.path.expanduser(os.path.join("~", ".gemini", "antigravity"))
    candidates.append(default_user_dir)
    candidates.append(os.path.join(CURRENT_DIR, "antigravity"))

    for c in candidates:
        if os.path.exists(c) and (os.path.exists(os.path.join(c, "brain")) or os.path.exists(os.path.join(c, "conversation_summaries.db"))):
            return os.path.abspath(c)

    return default_user_dir

def parse_workspace_info(ws_raw):
    """
    从 SQLite 或 transcript 中的 workspace_uris 精准解析工作区名与物理路径
    """
    if not ws_raw:
        return "全局 / 独立会话", "无本地工作区绑定"
    raw = str(ws_raw).strip()
    try:
        p = json.loads(raw)
        if isinstance(p, list) and len(p) > 0:
            raw = p[0]
    except Exception:
        pass
    
    decoded = urllib.parse.unquote(raw)
    decoded = decoded.replace("file:///", "").replace("file://", "")
    decoded = decoded.replace("/", "\\").strip('"[]\\ ')
    decoded = re.sub(r'^([a-zA-Z])%3[aA]', r'\1:', decoded)
    
    parts = [x for x in decoded.split('\\') if x]
    if not parts:
        return "全局 / 独立会话", "无本地工作区绑定"
    
    proj_name = parts[-1]
    return proj_name, decoded

def to_local_date(ts):
    if not ts:
        return ""
    try:
        ts_clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
        return dt.astimezone().strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]

def to_local_time(ts):
    if not ts:
        return ""
    try:
        ts_clean = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_clean)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts[:19].replace("T", " ")

def load_data(antigravity_dir):
    brain_dir = os.path.join(antigravity_dir, "brain")
    db_path = os.path.join(antigravity_dir, "conversation_summaries.db")

    meta = {}
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT conversation_id, title, last_modified_time, step_count, workspace_uris FROM conversation_summaries")
            for cid, title, lmt, steps, ws in cur.fetchall():
                meta[cid] = {
                    "title": title or "未命名会话",
                    "last_modified_time": lmt or "",
                    "step_count": steps or 0,
                    "workspace": ws or ""
                }
            conn.close()
        except Exception as e:
            print(f"[提示] 读取 SQLite 数据库失败: {e}", file=sys.stderr)

    pattern = os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")
    transcript_files = glob.glob(pattern)

    conversations = []
    daily_stats = defaultdict(lambda: {"input": 0, "cache": 0, "output": 0, "billed": 0, "turns": 0, "steps": 0})

    now_dt = datetime.now()
    today_str = now_dt.strftime("%Y-%m-%d")
    yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    seven_days_ago_str = (now_dt - timedelta(days=7)).strftime("%Y-%m-%d")

    for t_path in transcript_files:
        parts = t_path.split(os.sep)
        cid = parts[-4] if len(parts) >= 4 else "unknown"
        
        c_in, c_cache, c_out = 0, 0, 0
        active_steps = 0
        user_turns = 0
        has_official_tokens = False

        daily_breakdown = defaultdict(lambda: {
            "user_turns": 0,
            "step_count": 0,
            "billed_tokens": 0,
            "input_tokens": 0,
            "cache_read_tokens": 0,
            "output_tokens": 0
        })

        t_full_path = t_path.replace("transcript.jsonl", "transcript_full.jsonl")
        target_read_path = t_full_path if os.path.exists(t_full_path) else t_path

        step_records = []
        turns_list = []
        try:
            with open(target_read_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        step_records.append(d)
                        
                        # 统计真实对话轮数 (用户提问次数与内容)
                        if d.get("type") == "USER_INPUT" or d.get("source") == "USER_EXPLICIT":
                            user_turns += 1
                            raw_c = d.get("content") or ""
                            clean_c = re.sub(r'<ADDITIONAL_METADATA>[\s\S]*', '', raw_c)
                            clean_c = re.sub(r'<CONTEXT_SUMMARY>[\s\S]*?</CONTEXT_SUMMARY>', '', clean_c)
                            clean_c = re.sub(r'<[^>]+>', '', clean_c).strip()
                            clean_c = re.sub(r'\s+', ' ', clean_c)
                            if len(clean_c) > 120:
                                clean_c = clean_c[:120] + "..."
                            if not clean_c:
                                clean_c = "（用户发起了交互指令或包含附件）"
                            
                            t_day = to_local_date(d.get("created_at"))
                            t_time = to_local_time(d.get("created_at"))
                            turns_list.append({
                                "turn": user_turns,
                                "prompt": clean_c,
                                "day": t_day,
                                "time": t_time
                            })
                            if t_day and t_day != "历史更早":
                                daily_breakdown[t_day]["user_turns"] += 1

                        if "input_tokens" in d or "output_tokens" in d:
                            has_official_tokens = True
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

        if has_official_tokens:
            for d in step_records:
                if "input_tokens" in d or "output_tokens" in d:
                    active_steps += 1
                    s_day = to_local_date(d.get("created_at"))
                    i_tok = d.get("input_tokens") or 0
                    c_tok = d.get("cache_read_tokens") or 0
                    o_tok = d.get("output_tokens") or 0
                    b_tok = i_tok + o_tok
                    c_in += i_tok
                    c_cache += c_tok
                    c_out += o_tok
                    if s_day and s_day != "历史更早":
                        daily_breakdown[s_day]["step_count"] += 1
                        daily_breakdown[s_day]["input_tokens"] += i_tok
                        daily_breakdown[s_day]["cache_read_tokens"] += c_tok
                        daily_breakdown[s_day]["output_tokens"] += o_tok
                        daily_breakdown[s_day]["billed_tokens"] += b_tok
        else:
            cur_context_tokens = 0
            for d in step_records:
                active_steps += 1
                s_day = to_local_date(d.get("created_at"))
                c = d.get("content") or ""
                th = d.get("thinking") or ""
                tc = str(d.get("tool_calls") or "")
                c_tok = estimate_tokens_from_text(c)
                th_tok = estimate_tokens_from_text(th)
                tc_tok = estimate_tokens_from_text(tc)

                st = d.get("type")
                s_in, s_cache, s_out = 0, 0, 0
                if st == "PLANNER_RESPONSE":
                    step_out = th_tok + c_tok + tc_tok
                    s_out = step_out
                    if cur_context_tokens > 1024:
                        cache_part = int(cur_context_tokens * 0.85)
                        in_part = cur_context_tokens - cache_part
                        s_cache = cache_part
                        s_in = in_part
                    else:
                        s_in = cur_context_tokens
                    cur_context_tokens += step_out
                else:
                    cur_context_tokens += (c_tok + tc_tok)

                c_in += s_in
                c_cache += s_cache
                c_out += s_out
                if s_day and s_day != "历史更早":
                    daily_breakdown[s_day]["step_count"] += 1
                    daily_breakdown[s_day]["input_tokens"] += s_in
                    daily_breakdown[s_day]["cache_read_tokens"] += s_cache
                    daily_breakdown[s_day]["output_tokens"] += s_out
                    daily_breakdown[s_day]["billed_tokens"] += (s_in + s_out)

        c_meta = meta.get(cid, {
            "title": "未索引会话",
            "last_modified_time": "",
            "step_count": active_steps,
            "workspace": ""
        })

        billed = c_in + c_out
        total_context = c_in + c_cache + c_out
        cache_rate = (c_cache / (c_in + c_cache) * 100) if (c_in + c_cache) > 0 else 0

        lmt = c_meta["last_modified_time"]
        local_lmt = to_local_time(lmt)
        day_str = max(daily_breakdown.keys()) if daily_breakdown else (to_local_date(lmt) or "历史更早")

        # 汇总到全局 daily_stats
        for s_day, s_val in daily_breakdown.items():
            if s_day and s_day != "历史更早":
                daily_stats[s_day]["input"] += s_val["input_tokens"]
                daily_stats[s_day]["cache"] += s_val["cache_read_tokens"]
                daily_stats[s_day]["output"] += s_val["output_tokens"]
                daily_stats[s_day]["billed"] += s_val["billed_tokens"]
                daily_stats[s_day]["turns"] += s_val["user_turns"]
                daily_stats[s_day]["steps"] += s_val["step_count"]

        if today_str in daily_breakdown or yesterday_str in daily_breakdown or day_str == today_str or day_str == yesterday_str:
            time_category = "today"
        elif any(k >= seven_days_ago_str for k in daily_breakdown.keys()) or day_str >= seven_days_ago_str:
            time_category = "last7days"
        else:
            time_category = "older"

        final_steps = c_meta["step_count"] if c_meta["step_count"] > 0 else active_steps

        # 解析归属项目
        p_name, p_path = parse_workspace_info(c_meta["workspace"])

        conversations.append({
            "id": cid,
            "title": c_meta["title"],
            "last_modified_time": local_lmt or lmt,
            "day": day_str,
            "time_category": time_category,
            "user_turns": user_turns,
            "step_count": final_steps,
            "workspace": c_meta["workspace"],
            "project_name": p_name,
            "project_path": p_path,
            "input_tokens": c_in,
            "cache_read_tokens": c_cache,
            "output_tokens": c_out,
            "billed_tokens": billed,
            "total_context": total_context,
            "cache_hit_rate": round(cache_rate, 2),
            "is_official": has_official_tokens,
            "is_estimated": not has_official_tokens and (c_in > 0 or c_out > 0),
            "transcript_path": t_path.replace("\\", "/"),
            "turns_list": turns_list,
            "daily_breakdown": dict(daily_breakdown)
        })

    # 默认按新增对话轮数降序排列（轮数相同时按新增计费 Token 降序）
    conversations.sort(key=lambda x: (x["user_turns"], x["billed_tokens"]), reverse=True)

    # 聚合项目层级透视数据
    projects_map = {}
    for c in conversations:
        p_name = c["project_name"]
        p_path = c["project_path"]
        if p_name not in projects_map:
            projects_map[p_name] = {
                "name": p_name,
                "path": p_path,
                "conversations": [],
                "billed_tokens": 0,
                "input_tokens": 0,
                "cache_tokens": 0,
                "output_tokens": 0,
                "user_turns": 0,
                "step_count": 0,
                "last_modified_time": ""
            }
        p = projects_map[p_name]
        p["conversations"].append(c)
        p["billed_tokens"] += c["billed_tokens"]
        p["input_tokens"] += c["input_tokens"]
        p["cache_tokens"] += c["cache_read_tokens"]
        p["output_tokens"] += c["output_tokens"]
        p["user_turns"] += c["user_turns"]
        p["step_count"] += c["step_count"]
        if c["last_modified_time"] > p["last_modified_time"]:
            p["last_modified_time"] = c["last_modified_time"]

    projects_list = []
    for p in projects_map.values():
        p["conv_count"] = len(p["conversations"])
        tot_in_cache = p["input_tokens"] + p["cache_tokens"]
        p["cache_hit_rate"] = round((p["cache_tokens"] / tot_in_cache * 100), 2) if tot_in_cache > 0 else 0
        # 项目内的会话默认按新增对话轮数降序排列（轮数相同时按新增计费 Token 降序）
        p["conversations"].sort(key=lambda x: (x["user_turns"], x["billed_tokens"]), reverse=True)
        projects_list.append(p)

    # 项目列表默认按总对话轮数降序排列
    projects_list.sort(key=lambda x: (x["user_turns"], x["billed_tokens"]), reverse=True)

    total_convs = len(conversations)
    active_convs = [c for c in conversations if c["billed_tokens"] > 0 or c["cache_read_tokens"] > 0]
    total_input = sum(c["input_tokens"] for c in conversations)
    total_cache = sum(c["cache_read_tokens"] for c in conversations)
    total_output = sum(c["output_tokens"] for c in conversations)
    total_billed = total_input + total_output
    total_context_all = total_input + total_cache + total_output
    global_cache_rate = (total_cache / (total_input + total_cache) * 100) if (total_input + total_cache) > 0 else 0

    today_active_convs = [c for c in conversations if today_str in c["daily_breakdown"] and (c["daily_breakdown"][today_str]["user_turns"] > 0 or c["daily_breakdown"][today_str]["billed_tokens"] > 0 or c["daily_breakdown"][today_str]["step_count"] > 0)]
    today_summary = {
        "count": len(today_active_convs),
        "billed": daily_stats[today_str]["billed"],
        "input": daily_stats[today_str]["input"],
        "cache": daily_stats[today_str]["cache"],
        "output": daily_stats[today_str]["output"],
        "turns": daily_stats[today_str]["turns"],
        "steps": daily_stats[today_str]["steps"]
    }

    sorted_days = sorted([d for d in daily_stats.keys() if d != "历史更早"])
    trend_labels = sorted_days[-7:] if len(sorted_days) >= 7 else sorted_days
    trend_input = [daily_stats[d]["input"] for d in trend_labels]
    trend_cache = [daily_stats[d]["cache"] for d in trend_labels]
    trend_output = [daily_stats[d]["output"] for d in trend_labels]
    trend_billed = [daily_stats[d]["billed"] for d in trend_labels]
    trend_turns = [daily_stats[d]["turns"] for d in trend_labels]
    trend_steps = [daily_stats[d]["steps"] for d in trend_labels]

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today_str": today_str,
        "yesterday_str": yesterday_str,
        "scanned_path": antigravity_dir.replace("\\", "/"),
        "total_conversations": total_convs,
        "active_conversations": len(active_convs),
        "total_projects": len(projects_list),
        "total_input_tokens": total_input,
        "total_cache_tokens": total_cache,
        "total_output_tokens": total_output,
        "total_billed_tokens": total_billed,
        "total_context_tokens": total_context_all,
        "global_cache_rate": round(global_cache_rate, 2),
        "total_user_turns": sum(c["user_turns"] for c in conversations),
        "total_steps": sum(c["step_count"] for c in conversations),
        "today": today_summary,
        "trend": {
            "labels": trend_labels,
            "input": trend_input,
            "cache": trend_cache,
            "output": trend_output,
            "billed": trend_billed,
            "turns": trend_turns,
            "steps": trend_steps
        },
        "projects": projects_list,
        "top_15": conversations[:15],
        "snapshots": list_snapshots()
    }

    return summary, conversations

def generate_html(summary, conversations):
    json_summary = json.dumps(summary, ensure_ascii=False)
    json_conversations = json.dumps(conversations, ensure_ascii=False)

    html_template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Google Antigravity Token 消耗全景仪表盘 (项目透视与会话下钻版)</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              cyan: '#00d2ff',
              blue: '#3b82f6',
              purple: '#8b5cf6',
              amber: '#f59e0b',
              emerald: '#10b981'
            }
          }
        }
      }
    }
  </script>
  <style>
    *, *::before, *::after {
      transition: background-color 0.25s ease, border-color 0.25s ease, color 0.15s ease;
    }

    /* 灵感汲取自 StudyHelp 高级马卡龙极光工作台氛围 */
    html:not(.dark) body {
      background: 
        radial-gradient(at 0% 0%, rgba(204, 251, 241, 0.65) 0px, transparent 55%),
        radial-gradient(at 100% 0%, rgba(237, 233, 254, 0.75) 0px, transparent 55%),
        radial-gradient(at 50% 100%, rgba(254, 243, 199, 0.55) 0px, transparent 55%),
        #f5f8fc;
      color: #0f172a;
    }
    html:not(.dark) .theme-card {
      background: rgba(255, 255, 255, 0.88);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.95);
      box-shadow: 0 10px 30px -4px rgba(148, 163, 184, 0.15), 0 4px 8px -2px rgba(148, 163, 184, 0.06);
    }
    html:not(.dark) .theme-subtle { 
      background: rgba(241, 245, 249, 0.75); 
      border-color: rgba(226, 232, 240, 0.9); 
    }
    html:not(.dark) .theme-border { border-color: rgba(226, 232, 240, 0.9); }

    html.dark body { 
      background: 
        radial-gradient(at 0% 0%, rgba(14, 165, 233, 0.16) 0px, transparent 55%),
        radial-gradient(at 100% 0%, rgba(168, 85, 247, 0.16) 0px, transparent 55%),
        radial-gradient(at 50% 100%, rgba(16, 185, 129, 0.1) 0px, transparent 55%),
        #080c14; 
      color: #f1f5f9; 
    }
    html.dark .theme-card {
      background: rgba(15, 23, 42, 0.78);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-color: rgba(255, 255, 255, 0.08);
      box-shadow: 0 10px 30px -4px rgba(0, 0, 0, 0.5);
    }
    html.dark .theme-subtle { background-color: rgba(2, 6, 23, 0.45); border-color: rgba(255, 255, 255, 0.08); }
    html.dark .theme-border { border-color: rgba(255, 255, 255, 0.08); }

    .custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    html.dark .custom-scroll::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.5); }
    html.dark .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 9999px; }
    html:not(.dark) .custom-scroll::-webkit-scrollbar-track { background: rgba(241, 245, 249, 0.8); }
    html:not(.dark) .custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 9999px; }

    /* 高定马卡龙徽章体系 (与参考图完全一致的高饱和软胶囊) */
    .badge-high { background: rgba(254, 226, 226, 0.9); color: #dc2626; border: 1px solid rgba(252, 165, 165, 0.8); font-weight: 700; }
    .badge-mid { background: rgba(254, 243, 199, 0.9); color: #d97706; border: 1px solid rgba(252, 211, 77, 0.8); font-weight: 700; }
    .badge-low { background: rgba(209, 250, 229, 0.9); color: #059669; border: 1px solid rgba(110, 231, 183, 0.8); font-weight: 700; }
    .badge-est { background: rgba(237, 233, 254, 0.9); color: #7c3aed; border: 1px solid rgba(196, 181, 253, 0.8); font-weight: 700; }
    
    html.dark .badge-high { background: rgba(239, 68, 68, 0.2); color: #f87171; border-color: rgba(239, 68, 68, 0.4); }
    html.dark .badge-mid { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border-color: rgba(245, 158, 11, 0.4); }
    html.dark .badge-low { background: rgba(16, 185, 129, 0.2); color: #34d399; border-color: rgba(16, 185, 129, 0.4); }
    html.dark .badge-est { background: rgba(139, 92, 246, 0.2); color: #c084fc; border-color: rgba(139, 92, 246, 0.4); }

    .summary-row {
      background: linear-gradient(90deg, rgba(59, 130, 246, 0.2), rgba(0, 210, 255, 0.14), rgba(139, 92, 246, 0.12));
      border-bottom: 2px solid rgba(0, 210, 255, 0.4);
    }
    html:not(.dark) .summary-row {
      background: linear-gradient(90deg, rgba(219, 234, 254, 0.95), rgba(224, 242, 254, 0.95), rgba(237, 233, 254, 0.9));
      border-bottom: 2px solid #0ea5e9;
    }

    .time-pill.active, .proj-time-pill.active {
      background: linear-gradient(135deg, #2563eb 0%, #0284c7 100%);
      color: #ffffff !important;
      box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
    }

    .view-pill.active {
      background: linear-gradient(135deg, #4f46e5 0%, #06b6d4 100%);
      color: #ffffff !important;
      box-shadow: 0 6px 18px 0 rgba(79, 70, 229, 0.35);
    }

    /* 视图切换按钮徽章的高对比度方案 (彻底杜绝字色融入背景) */
    .view-pill .view-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 1.5px 7px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 800;
      line-height: 1.2;
      transition: all 0.2s ease;
      background: rgba(148, 163, 184, 0.25);
      color: #334155;
    }
    html.dark .view-pill .view-badge {
      background: rgba(255, 255, 255, 0.18);
      color: #f1f5f9;
    }
    /* 激活态：极光高光白底 + 靛青粗体字，对比度 100%，清晰锐利 */
    .view-pill.active .view-badge {
      background: #ffffff !important;
      color: #3730a3 !important;
      font-weight: 900 !important;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
    }

    /* 时间过滤胶囊内徽章的高对比度方案 */
    .time-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 1px 6px;
      border-radius: 6px;
      font-size: 10px;
      font-weight: 800;
      line-height: 1.2;
      transition: all 0.2s ease;
      background: rgba(59, 130, 246, 0.15);
      color: #2563eb;
    }
    html.dark .time-badge {
      background: rgba(59, 130, 246, 0.3);
      color: #93c5fd;
    }
    .time-pill.active .time-badge {
      background: #ffffff !important;
      color: #1d4ed8 !important;
      font-weight: 900 !important;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
    }
    ::selection {
      background-color: #2563eb !important;
      color: #ffffff !important;
    }
    ::-moz-selection {
      background-color: #2563eb !important;
      color: #ffffff !important;
    }
  </style>
</head>
<body class="min-h-screen font-sans antialiased selection:bg-blue-600 selection:text-white dark:selection:bg-blue-500 dark:selection:text-white">

  <!-- 顶部状态栏与主题切换 (StudyHelp 风格高透毛玻璃) -->
  <header class="border-b theme-border backdrop-blur-xl sticky top-0 z-30 bg-white/70 dark:bg-slate-950/70 shadow-sm">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      
      <!-- Logo与标题 -->
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-blue-600 via-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/25">
          <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-black tracking-tight text-slate-900 dark:text-white">
              Antigravity Token 消耗全景仪表盘
            </h1>
            <span class="px-2.5 py-0.5 text-[10px] font-black rounded-full bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-sm shadow-indigo-500/20">
              V7.0 极光灵动版
            </span>
          </div>
          <p class="text-xs text-slate-500 dark:text-slate-400 hidden sm:block">
            按工作区/项目聚合透视 · 支持具体会话下钻 · 独立统计新增 Token 与对话轮数
          </p>
        </div>
      </div>

      <!-- 右侧控制区 (快照回溯与多彩胶囊按钮群) -->
      <div class="flex items-center gap-2 sm:gap-3">
        <!-- 快照时间点标记与历史回溯下拉选择器 -->
        <div class="relative flex items-center" id="snapshotControlGroup">
          <div class="flex items-center gap-2 px-3 py-1.5 rounded-xl bg-slate-100/90 dark:bg-slate-800/90 border theme-border text-xs shadow-xs text-slate-700 dark:text-slate-200 hover:border-cyan-400/60 transition-all">
            <span class="relative flex h-2 w-2">
              <span id="snapshotPing" class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span id="snapshotDot" class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <span class="text-slate-400 text-[11px] font-medium hidden md:inline shrink-0">数据时间:</span>
            <div class="relative flex items-center">
              <select id="snapshotSelect" onchange="switchSnapshot(this.value)" class="bg-transparent font-mono font-bold text-xs text-slate-800 dark:text-slate-100 focus:outline-none cursor-pointer appearance-none pr-5 pl-0.5" title="点击切换并回溯历史扫描快照">
                <option value="current">${summary.get('generated_at', '')} (当前最新)</option>
              </select>
              <div class="pointer-events-none absolute right-0 flex items-center text-slate-400">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </div>
            </div>
          </div>
        </div>

        <button id="btnRefreshData" onclick="refreshDashboardData()" class="p-2 sm:px-3 sm:py-1.5 rounded-xl bg-cyan-50 dark:bg-cyan-950/40 hover:bg-cyan-100 text-cyan-700 dark:text-cyan-300 border border-cyan-200 dark:border-cyan-800 text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all" title="点击由服务端重新深度扫描全量会话并无刷新同步">
          <svg id="refreshSpinner" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
          <span class="hidden sm:inline" id="refreshText">重新扫描</span>
        </button>

        <button onclick="toggleTheme()" class="p-2 sm:px-3 sm:py-1.5 rounded-xl bg-slate-100/80 dark:bg-slate-800/80 border theme-border text-xs font-semibold text-slate-700 dark:text-slate-300 hover:text-indigo-600 flex items-center gap-1.5 shadow-sm transition-all">
          <span id="themeIcon">☀️</span>
          <span id="themeText" class="hidden sm:inline">浅色模式</span>
        </button>

        <button onclick="exportToCSV()" class="p-2 sm:px-3.5 sm:py-1.5 rounded-xl bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white text-xs font-bold flex items-center gap-1.5 shadow-md shadow-indigo-500/25 transition-all">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
          <span class="hidden sm:inline">导出 CSV</span>
        </button>
      </div>

    </div>
  </header>

  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">

    <!-- 顶部核心指标 KPI 卡片群 (StudyHelp 风格高饱和马卡龙五色卡片) -->
    <section class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
      
      <!-- 卡片 1: 累计新增 Token (冰蓝软卡) -->
      <div class="rounded-2xl p-5 relative overflow-hidden group border-2 transition-all duration-300 hover:shadow-xl hover:-translate-y-0.5 bg-[#ebf5ff] dark:bg-blue-950/40 border-[#bfdbfe] dark:border-blue-800/60 shadow-[0_4px_16px_rgba(59,130,246,0.12)]">
        <div class="flex items-center justify-between">
          <span class="text-xs font-black uppercase tracking-wider text-blue-900 dark:text-blue-300">累计新增 Token</span>
          <span class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-blue-600 to-indigo-600 text-white shadow-md shadow-blue-500/35 flex items-center justify-center">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          </span>
        </div>
        <div class="mt-3">
          <div class="text-3xl font-black text-blue-600 dark:text-cyan-400 tracking-tight font-mono" id="kpiBilled">-</div>
          <div class="text-xs text-blue-700/75 dark:text-blue-300/70 mt-1 font-mono truncate" id="kpiBilledRaw">- tokens</div>
        </div>
      </div>

      <!-- 卡片 2: 缓存命中 Token (暖金琥珀软卡) -->
      <div class="rounded-2xl p-5 relative overflow-hidden group border-2 transition-all duration-300 hover:shadow-xl hover:-translate-y-0.5 bg-[#fff8e6] dark:bg-amber-950/40 border-[#fed7aa] dark:border-amber-800/60 shadow-[0_4px_16px_rgba(245,158,11,0.12)]">
        <div class="flex items-center justify-between">
          <span class="text-xs font-black uppercase tracking-wider text-amber-950 dark:text-amber-300">缓存节省 Token</span>
          <span class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-amber-500 to-orange-500 text-white shadow-md shadow-amber-500/35 flex items-center justify-center">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
          </span>
        </div>
        <div class="mt-3">
          <div class="text-3xl font-black text-amber-500 dark:text-amber-400 tracking-tight font-mono" id="kpiCache">-</div>
          <div class="text-xs text-amber-800/75 dark:text-amber-300/70 mt-1 font-mono truncate" id="kpiCacheRaw">- tokens</div>
        </div>
      </div>

      <!-- 卡片 3: 全量对话总轮数与均耗 (香芋丁香紫软卡 · 严格上下两行) -->
      <div class="rounded-2xl p-5 relative overflow-hidden group border-2 transition-all duration-300 hover:shadow-xl hover:-translate-y-0.5 bg-[#f4effe] dark:bg-purple-950/40 border-[#ddd6fe] dark:border-purple-800/60 shadow-[0_4px_16px_rgba(147,51,234,0.12)]">
        <div class="flex items-center justify-between">
          <span class="text-xs font-black uppercase tracking-wider text-purple-950 dark:text-purple-300">全量对话总轮数</span>
          <span class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-purple-600 to-violet-500 text-white shadow-md shadow-purple-500/35 flex items-center justify-center">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/></svg>
          </span>
        </div>
        <div class="mt-3">
          <!-- 上一行: 大数字 870 轮 -->
          <div class="flex items-baseline gap-1 whitespace-nowrap">
            <span class="text-3xl font-black text-purple-950 dark:text-white tracking-tight font-mono" id="kpiTotalTurns">-</span>
            <span class="text-xs text-purple-700 dark:text-purple-300 font-bold">轮</span>
          </div>
          <!-- 下一行: 均耗 ~1.45 M/轮 -->
          <div class="text-xs font-mono mt-1 whitespace-nowrap flex items-center">
            <span class="text-purple-700/75 dark:text-purple-300/75 font-medium">均耗:</span>
            <span class="text-xs font-black text-purple-700 dark:text-purple-300 font-mono ml-1" id="kpiAvgTokensPerTurn">-</span>
          </div>
          <!-- 底部明细 -->
          <div class="text-[11px] text-purple-800/70 dark:text-purple-300/70 mt-2 flex items-center justify-between border-t border-purple-200/80 dark:border-purple-800/60 pt-1.5 whitespace-nowrap">
            <span class="whitespace-nowrap">累计步骤: <span class="font-bold text-purple-950 dark:text-purple-200" id="kpiTotalSteps">- 步</span></span>
            <span class="whitespace-nowrap"><span class="font-bold text-purple-950 dark:text-purple-200" id="kpiTotalConvs">-</span> 会话</span>
          </div>
        </div>
      </div>

      <!-- 卡片 4: 今日/近24小时消耗 (青翠水蓝软卡) -->
      <div class="rounded-2xl p-5 relative overflow-hidden group border-2 transition-all duration-300 hover:shadow-xl hover:-translate-y-0.5 bg-[#e6fafc] dark:bg-cyan-950/40 border-[#a5f3fc] dark:border-cyan-800/60 shadow-[0_4px_16px_rgba(6,182,212,0.12)] cursor-pointer hover:scale-[1.02]" onclick="setTimeFilter('today')" title="点击查看今日明细">
        <div class="flex items-center justify-between">
          <span class="text-xs font-black uppercase tracking-wider text-cyan-950 dark:text-cyan-300">今日新增 Token</span>
          <span class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-cyan-500 to-teal-500 text-white shadow-md shadow-cyan-500/35 flex items-center justify-center">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
          </span>
        </div>
        <div class="mt-3">
          <div class="text-3xl font-black text-cyan-600 dark:text-cyan-400 tracking-tight font-mono" id="kpiTodayBilled">-</div>
          <div class="text-xs text-cyan-800/80 dark:text-cyan-300/80 mt-1 flex items-center justify-between whitespace-nowrap">
            <span id="kpiTodayCount" class="font-semibold">- 个会话</span>
            <span class="text-cyan-600 dark:text-cyan-400 font-extrabold underline whitespace-nowrap">筛选今日 ›</span>
          </div>
        </div>
      </div>

      <!-- 卡片 5: 今日新增对话轮数 (嫩芽翠绿软卡 · 严格上下两行) -->
      <div class="rounded-2xl p-5 relative overflow-hidden group border-2 transition-all duration-300 hover:shadow-xl hover:-translate-y-0.5 bg-[#edfcf2] dark:bg-emerald-950/40 border-[#bbf7d0] dark:border-emerald-800/60 shadow-[0_4px_16px_rgba(16,185,129,0.12)] cursor-pointer hover:scale-[1.02]" onclick="setTimeFilter('today')" title="点击查看今日提问与交互流水">
        <div class="flex items-center justify-between">
          <span class="text-xs font-black uppercase tracking-wider text-emerald-950 dark:text-emerald-300">今日新增对话轮数</span>
          <span class="w-10 h-10 rounded-2xl bg-gradient-to-tr from-emerald-500 to-teal-600 text-white shadow-md shadow-emerald-500/35 flex items-center justify-center">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6"/></svg>
          </span>
        </div>
        <div class="mt-3">
          <!-- 上一行: 大数字 +25 轮 -->
          <div class="flex items-baseline gap-1 whitespace-nowrap">
            <span class="text-3xl font-black text-emerald-600 dark:text-emerald-400 tracking-tight font-mono" id="kpiTodayTurns">+0</span>
            <span class="text-xs font-bold text-emerald-700 dark:text-emerald-300 whitespace-nowrap">轮</span>
          </div>
          <!-- 下一行: 新增 Token: +6.54 M -->
          <div class="text-xs font-mono mt-1 whitespace-nowrap flex items-center">
            <span class="text-emerald-800/75 dark:text-emerald-300/75 font-medium">新增 Token:</span>
            <span class="text-xs font-black text-emerald-600 dark:text-cyan-400 font-mono ml-1" id="kpiTodayNewTokens">+0</span>
          </div>
          <!-- 底部明细 -->
          <div class="text-[11px] text-emerald-800/70 dark:text-emerald-300/70 mt-2 flex items-center justify-between border-t border-emerald-200/80 dark:border-emerald-800/60 pt-1.5 whitespace-nowrap">
            <span class="whitespace-nowrap">自主动作: <span class="font-bold text-emerald-950 dark:text-emerald-200" id="kpiTodaySteps">+0 步</span></span>
            <span class="text-emerald-700 dark:text-emerald-300 font-bold whitespace-nowrap" id="kpiTodayAvgTokens">均 0/轮</span>
          </div>
        </div>
      </div>

    </section>

    <!-- 可视化图表区 -->
    <section class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      
      <!-- 趋势图 (近 7 日) -->
      <div class="lg:col-span-2 theme-card rounded-2xl p-6 border flex flex-col justify-between">
        <div>
          <div class="flex items-center justify-between mb-4">
            <div>
              <h2 class="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-blue-500"></span>
                近 7 日 Token 消耗趋势
              </h2>
              <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">净计费 Token 与 缓存命中 Token 日度波动</p>
            </div>
            <div class="flex items-center gap-2 text-xs">
              <span class="inline-flex items-center gap-1.5 text-slate-600 dark:text-slate-300">
                <span class="w-3 h-3 rounded bg-blue-500 inline-block"></span> 计费 Token
              </span>
              <span class="inline-flex items-center gap-1.5 text-slate-600 dark:text-slate-300 ml-2">
                <span class="w-3 h-3 rounded bg-amber-400 inline-block"></span> 缓存命中
              </span>
            </div>
          </div>
          <div class="h-60 relative" id="trendChartContainer">
            <canvas id="trendChart"></canvas>
            <!-- 锁定提示框浮层 (Pinned Tooltip Card，支持点击锁定，鼠标自由乱动不消失，绝不跳页) -->
            <div id="trendChartPinnedTooltip" class="hidden absolute z-30 pointer-events-auto transition-all duration-150 select-text"></div>
          </div>
        </div>

        <!-- 近 7 日每日具体用量胶囊速览条 (用户可直观看到每个的用量) -->
        <div class="mt-4 pt-3 border-t theme-border">
          <div class="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400 mb-2">
            <span class="font-semibold flex items-center gap-1.5">
              <span>📅 近 7 日每日具体用量速览</span>
              <span class="text-[10px] text-slate-400 font-normal hidden sm:inline">(点击任一天可精准筛选对应会话)</span>
            </span>
            <span class="text-[11px] text-blue-500 dark:text-cyan-400 font-mono">计费 Token / 缓存读取</span>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-7 gap-1.5 text-center" id="dailyTrendPills">
            <!-- 动态渲染 7 天每日具体用量 -->
          </div>
        </div>
      </div>

      <!-- 环形比例图 -->
      <div class="theme-card rounded-2xl p-6 flex flex-col justify-between border">
        <div class="mb-2">
          <h2 class="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-indigo-500"></span>
            Token 全局结构构成
          </h2>
          <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">新增输入 vs 缓存命中 vs 生成输出</p>
        </div>
        <div class="h-52 relative flex items-center justify-center">
          <canvas id="ratioChart"></canvas>
        </div>
        <div class="grid grid-cols-3 gap-2 text-center text-xs mt-3 pt-3 border-t theme-border">
          <div class="p-2 rounded-xl bg-blue-50/80 dark:bg-blue-950/40 border border-blue-200/60 dark:border-blue-800/40 shadow-sm">
            <div class="text-[10px] font-bold text-blue-700 dark:text-blue-300">新增输入</div>
            <div class="font-black text-blue-900 dark:text-blue-100 font-mono mt-0.5" id="pieInputVal">-</div>
          </div>
          <div class="p-2 rounded-xl bg-amber-50/80 dark:bg-amber-950/40 border border-amber-200/60 dark:border-amber-800/40 shadow-sm">
            <div class="text-[10px] font-bold text-amber-700 dark:text-amber-300">缓存读取</div>
            <div class="font-black text-amber-900 dark:text-amber-100 font-mono mt-0.5" id="pieCacheVal">-</div>
          </div>
          <div class="p-2 rounded-xl bg-emerald-50/80 dark:bg-emerald-950/40 border border-emerald-200/60 dark:border-emerald-800/40 shadow-sm">
            <div class="text-[10px] font-bold text-emerald-700 dark:text-emerald-300">模型生成</div>
            <div class="font-black text-emerald-900 dark:text-emerald-100 font-mono mt-0.5" id="pieOutputVal">-</div>
          </div>
        </div>
      </div>

    </section>

    <!-- 核心工作区：双视角工作台 (全量会话明细 vs 按项目透视) -->
    <section class="theme-card rounded-2xl p-6 border space-y-6">
      
      <!-- 视角切换与全局单行极简控制工具栏 (View Tabs & Strictly Single-Line Toolbar) -->
      <div class="flex items-center justify-between gap-3 border-b theme-border pb-3 overflow-x-auto flex-nowrap select-none [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
        
        <!-- 左侧区: 视角切换与紧凑检索 (靠左) -->
        <div class="flex items-center gap-2.5 shrink-0 select-none">
          <!-- 1. 视角切换卡 (饱满大方版) -->
          <div class="flex items-center gap-1 p-1 rounded-2xl theme-subtle border shadow-sm backdrop-blur-md shrink-0">
            <button id="viewTabSession" onclick="switchView('session')" class="view-pill active px-3.5 py-1.5 rounded-xl text-xs sm:text-sm font-bold transition-all flex items-center gap-1.5 shadow-sm whitespace-nowrap">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
              <span>会话明细</span>
              <span class="view-badge" id="badgeSessionCount">-</span>
            </button>
            
            <button id="viewTabProject" onclick="switchView('project')" class="view-pill px-3.5 py-1.5 rounded-xl text-xs sm:text-sm font-bold text-slate-600 dark:text-slate-300 hover:text-indigo-600 transition-all flex items-center gap-1.5 whitespace-nowrap">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
              <span>项目透视</span>
              <span class="view-badge" id="badgeProjectCount">-</span>
            </button>
          </div>

          <!-- 2. 搜索框 (仅会话视角显示) -->
          <div id="sessionSearchWrapper" class="relative w-36 sm:w-44 md:w-48 shrink-0">
            <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none text-slate-400">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
            </span>
            <input type="text" id="searchInput" placeholder="搜索会话..." 
                   class="w-full pl-8 pr-3 py-1.5 text-xs sm:text-sm rounded-xl theme-subtle border text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:border-cyan-500 transition-all">
          </div>

          <!-- 2. 搜索框 (仅项目视角显示) -->
          <div id="projectSearchWrapper" class="hidden relative w-36 sm:w-44 md:w-48 shrink-0">
            <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none text-slate-400">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
            </span>
            <input type="text" id="projectSearchInput" placeholder="过滤项目..." 
                   class="w-full pl-8 pr-3 py-1.5 text-xs sm:text-sm rounded-xl theme-subtle border text-slate-900 dark:text-white placeholder-slate-400 focus:outline-none focus:border-cyan-500 transition-all">
          </div>

          <!-- 3. 特定日期筛选提示徽章 (全局唯一单例，杜绝双徽章并排显示) -->
          <div id="globalSpecificDateBadge" class="hidden items-center gap-1.5 px-2.5 py-1 rounded-xl bg-blue-500/15 border border-blue-500/30 text-blue-600 dark:text-cyan-400 text-xs font-semibold whitespace-nowrap shrink-0 select-none">
            <span id="globalSpecificDateText">📅 限定日期</span>
            <button onclick="clearSpecificDate()" class="hover:text-red-500 font-bold ml-0.5 text-xs cursor-pointer" title="清除此日期限定">✕</button>
          </div>
        </div>

        <!-- 右侧区: 展开操作 + 时间胶囊 + 排序/筛选下拉组 (严格靠右对齐 ml-auto) -->
        <div class="flex items-center gap-2.5 shrink-0 ml-auto select-none">
          <!-- 4. 展开/折叠二合一按钮 (仅在项目视角下展示) -->
          <button onclick="toggleAllProjectsCombined()" id="btnToggleAllProjects" 
                  class="hidden px-3.5 py-1.5 rounded-xl theme-subtle border text-xs sm:text-sm font-bold text-slate-700 dark:text-slate-300 hover:text-cyan-500 hover:border-cyan-500 transition-all items-center gap-1.5 shadow-sm select-none whitespace-nowrap shrink-0">
            <svg class="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 13l-7 7-7-7m14-8l-7 7-7-7"/></svg>
            <span>全部展开</span>
          </button>

          <!-- 5. 时间快捷分类胶囊 (饱满大方版) -->
          <div class="flex items-center gap-1 p-1 rounded-2xl theme-subtle border select-none whitespace-nowrap shrink-0">
            <button onclick="setTimeFilter('all')" id="pill-all" class="time-pill active px-3 py-1.5 rounded-xl text-xs sm:text-sm font-semibold border border-transparent transition-all">
              全部时间
            </button>
            <button onclick="setTimeFilter('today')" id="pill-today" class="time-pill px-3 py-1.5 rounded-xl text-xs sm:text-sm font-semibold text-slate-600 dark:text-slate-300 hover:text-blue-500 border border-transparent transition-all flex items-center gap-1.5">
              <span>📅 近 24 小时</span>
              <span class="time-badge" id="badgeTodayCount">0</span>
            </button>
            <button onclick="setTimeFilter('last7days')" id="pill-last7days" class="time-pill px-3 py-1.5 rounded-xl text-xs sm:text-sm font-semibold text-slate-600 dark:text-slate-300 hover:text-blue-500 border border-transparent transition-all">
              近 7 天
            </button>
            <button onclick="setTimeFilter('older')" id="pill-older" class="time-pill px-3 py-1.5 rounded-xl text-xs sm:text-sm font-semibold text-slate-600 dark:text-slate-300 hover:text-blue-500 border border-transparent transition-all">
              更早历史
            </button>
          </div>

          <!-- 6. 项目排序按钮 (仅在项目视角下展示，外观固定精简为“排序”二字) -->
          <div id="projectSortWrapper" class="hidden relative items-center shrink-0 group">
            <div class="py-1.5 px-3 rounded-xl theme-subtle border text-slate-700 dark:text-slate-300 group-hover:text-cyan-500 group-hover:border-cyan-500 transition-all text-xs sm:text-sm font-semibold flex items-center gap-1.5 shadow-sm whitespace-nowrap cursor-pointer select-none">
              <svg class="w-3.5 h-3.5 text-blue-500 group-hover:text-cyan-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4h13M3 8h9m-9 4h6m4 0l4-4m0 0l4 4m-4-4v12"/></svg>
              <span>排序</span>
              <svg class="w-3 h-3 text-slate-400 group-hover:text-cyan-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
            </div>
            <select id="projectSortSelect" onchange="renderProjectsView()" title="选择项目排序方式" class="absolute inset-0 opacity-0 w-full h-full cursor-pointer">
              <option value="turns" selected>按对话轮数 (降序)</option>
              <option value="billed">按新增 Token (降序)</option>
              <option value="convs">按会话数 (降序)</option>
              <option value="time">按活跃时间 (降序)</option>
            </select>
          </div>

          <!-- 7. 全量会话过滤下拉框组 (仅在会话视角下展示，紧跟时间胶囊右侧) -->
          <div id="sessionFilterGroup" class="flex items-center gap-1.5 shrink-0">
            <select id="filterSource" class="py-1.5 px-2.5 text-xs sm:text-sm rounded-xl theme-subtle border text-slate-700 dark:text-slate-300 focus:outline-none focus:border-cyan-500">
              <option value="all">类型: 全部</option>
              <option value="official">官方实测</option>
              <option value="estimated">历史估算</option>
            </select>

            <select id="filterLevel" class="py-1.5 px-2.5 text-xs sm:text-sm rounded-xl theme-subtle border text-slate-700 dark:text-slate-300 focus:outline-none focus:border-cyan-500">
              <option value="all">所有消耗</option>
              <option value="high">>3M (重度)</option>
              <option value="mid">500K-3M (中度)</option>
              <option value="low"><500K (轻度)</option>
            </select>

            <select id="pageSizeSelect" class="py-1.5 px-2.5 text-xs sm:text-sm rounded-xl theme-subtle border text-slate-700 dark:text-slate-300 focus:outline-none focus:border-cyan-500">
              <option value="15">15 条/页</option>
              <option value="30">30 条/页</option>
              <option value="50">50 条/页</option>
              <option value="all">全部</option>
            </select>
          </div>
        </div>

      </div>

      <!-- 视角 A: 全量会话明细容器 -->
      <div id="sessionViewContainer" class="space-y-4">

        <!-- 表格主体 -->
        <div class="overflow-x-auto rounded-xl border theme-border">
          <table class="w-full text-left text-xs md:text-sm whitespace-nowrap">
            <thead class="theme-subtle text-slate-500 dark:text-slate-400 border-b theme-border font-medium whitespace-nowrap">
              <tr>
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 whitespace-nowrap" onclick="changeSort('title')">
                  会话标题与所属项目 <span id="sort_title" class="text-xs"></span>
                </th>
                <!-- 核心指标 1: 新增 Token -->
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-right whitespace-nowrap" onclick="changeSort('billed_tokens')" title="实际扣除配额的新增计费 Token (输入+输出)">
                  新增 Token <span id="sort_billed_tokens" class="text-xs"></span>
                </th>
                <!-- 核心指标 2: 新增对话轮数 (默认排序列) -->
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-center whitespace-nowrap" onclick="changeSort('user_turns')" title="用户发起的真实提问回合数">
                  新增对话轮数 <span id="sort_user_turns" class="text-xs text-blue-600 dark:text-cyan-400 font-bold">▼</span>
                </th>
                <!-- 核心指标 3: 执行步骤数 -->
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-center whitespace-nowrap" onclick="changeSort('step_count')" title="Agent 幕后原子动作总数">
                  执行步骤数 <span id="sort_step_count" class="text-xs"></span>
                </th>
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-right whitespace-nowrap" onclick="changeSort('cache_read_tokens')">
                  缓存命中 <span id="sort_cache_read_tokens" class="text-xs"></span>
                </th>
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-right whitespace-nowrap" onclick="changeSort('input_tokens')">
                  Prompt 输入 <span id="sort_input_tokens" class="text-xs"></span>
                </th>
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-right whitespace-nowrap" onclick="changeSort('output_tokens')">
                  生成输出 <span id="sort_output_tokens" class="text-xs"></span>
                </th>
                <th class="py-3 px-4 cursor-pointer hover:text-blue-500 text-right whitespace-nowrap" onclick="changeSort('last_modified_time')">
                  更新时间 <span id="sort_last_modified_time" class="text-xs"></span>
                </th>
                <th class="py-3 px-4 text-center whitespace-nowrap">操作</th>
              </tr>
            </thead>
            
            <!-- 置顶动态总计行 -->
            <tbody id="summaryTableBody">
              <tr class="summary-row font-semibold whitespace-nowrap">
                <td class="py-3.5 px-4 text-blue-600 dark:text-cyan-300 whitespace-nowrap">
                  <span class="px-2 py-0.5 rounded text-[11px] font-bold bg-cyan-500 text-white shadow-sm mr-2">总计</span>
                  <span id="summaryTimePrefixText">当前筛选</span> 共 <span id="summaryTotalCount" class="font-extrabold text-slate-900 dark:text-white">131</span> 个会话合计数
                </td>
                <!-- 新增 Token 汇总 -->
                <td class="py-3.5 px-4 text-right font-extrabold text-blue-600 dark:text-cyan-400 font-mono text-sm whitespace-nowrap" id="summaryBilled">-</td>
                <!-- 新增对话轮数 汇总 -->
                <td class="py-3.5 px-4 text-center font-extrabold text-blue-600 dark:text-cyan-400 font-mono text-sm whitespace-nowrap" id="summaryTurns">-</td>
                <!-- 执行步骤数 汇总 -->
                <td class="py-3.5 px-4 text-center font-bold text-slate-700 dark:text-slate-300 font-mono text-sm whitespace-nowrap" id="summarySteps">-</td>
                <!-- 缓存命中 -->
                <td class="py-3.5 px-4 text-right font-bold text-amber-500 font-mono whitespace-nowrap" id="summaryCache">-</td>
                <!-- Prompt 输入 -->
                <td class="py-3.5 px-4 text-right font-bold text-blue-500 font-mono whitespace-nowrap" id="summaryInput">-</td>
                <!-- 生成输出 -->
                <td class="py-3.5 px-4 text-right font-bold text-emerald-500 font-mono whitespace-nowrap" id="summaryOutput">-</td>
                <td class="py-3.5 px-4 text-right text-xs text-slate-500 whitespace-nowrap" id="summaryTimeRangeTag">实时汇总</td>
                <td class="py-3.5 px-4 text-center whitespace-nowrap">
                  <span class="text-[11px] text-slate-400 font-normal">动态计算</span>
                </td>
              </tr>
            </tbody>

            <!-- 数据明细行 -->
            <tbody id="tableBody" class="divide-y theme-border text-slate-700 dark:text-slate-300">
            </tbody>
          </table>
        </div>

        <!-- 分页条 -->
        <div class="flex flex-col sm:flex-row items-center justify-between gap-4 mt-4 pt-2">
          <div class="text-xs text-slate-500 dark:text-slate-400" id="pageInfoTag">
            正在展示第 1 至 15 条
          </div>
          <div class="flex items-center gap-1.5" id="paginationControls">
          </div>
        </div>

      </div>

      <!-- 视角 B: 按项目聚合透视容器 (项目手风琴与具体会话下钻) -->
      <div id="projectViewContainer" class="space-y-5 hidden">
        


        <!-- 项目卡片手风琴列表容器 -->
        <div id="projectsAccordionList" class="space-y-4">
        </div>

      </div>

    </section>

  </main>

  <!-- 侧边详情抽屉 -->
  <div id="drawerOverlay" class="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 hidden transition-opacity" onclick="closeDrawer()"></div>
  <aside id="drawer" 
         class="theme-card border-l theme-border shadow-2xl z-50 transform translate-x-full transition-transform duration-300 ease-in-out hidden flex flex-col justify-between"
         style="position: fixed !important; top: 0 !important; right: 0 !important; bottom: 0 !important; height: 100vh !important; width: 780px; max-width: 96vw; z-index: 9999;">
    
    <!-- 抽屉左侧可拖拽伸缩手柄与凸起调节按钮 (用户原话：“给我新加一个按钮，我可以左右拉动，从而控制整个看板的大小”) -->
    <div id="drawerResizeHandle" 
         class="absolute -left-3 top-0 bottom-0 w-6 cursor-ew-resize hover:bg-cyan-500/20 active:bg-cyan-500/40 transition-colors z-50 flex items-center justify-center group select-none" 
         title="按住鼠标左键左右拖拽，自由调整界面大小；点击可快速切换大屏/标准屏">
      <!-- 贯通上下的柔和高光导轨 -->
      <div class="w-1 h-full bg-slate-300/40 dark:bg-slate-700/60 group-hover:bg-cyan-400 transition-colors"></div>
      
      <!-- 居中醒目的左右拖动调节按钮 (Pill Button) -->
      <div id="drawerResizePill" 
           class="absolute top-1/2 -translate-y-1/2 -left-4 px-2 py-3 rounded-full bg-gradient-to-r from-blue-600 to-cyan-500 text-white shadow-xl shadow-cyan-500/40 border border-white/30 flex flex-col items-center justify-center gap-1 cursor-ew-resize group-hover:scale-110 active:scale-95 transition-transform"
           title="按住左右拖拽：自由调节看板大小&#10;单击：一键切换大屏 / 标准宽度">
        <svg class="w-3.5 h-3.5 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M8 7l-5 5 5 5m8-10l5 5-5 5"/>
        </svg>
        <span class="text-[9px] font-black tracking-tighter uppercase select-none opacity-95 leading-none" style="writing-mode: vertical-rl;">拉动</span>
      </div>
    </div>

    <!-- 顶部固定标题栏 (已整合原“三”的复制 DOpus / 复制 Explorer 命令，绝不额外多占行) -->
    <div class="px-4.5 py-3 border-b theme-border flex items-center justify-between shrink-0 bg-gradient-to-r from-white via-indigo-50/30 to-purple-50/30 dark:from-slate-900 dark:via-indigo-950/20 dark:to-purple-950/20 backdrop-blur-xl">
      <div class="flex items-center gap-2 min-w-0">
        <span class="w-3 h-3 rounded-full bg-cyan-500 shadow-sm shadow-cyan-500/50 shrink-0"></span>
        <h3 class="font-black text-sm md:text-base text-slate-900 dark:text-white truncate">会话详细账本与逐轮透视</h3>
        <span id="drawerContextBadge" class="ml-1 px-2.5 py-0.5 rounded-full text-[11px] font-black bg-blue-500/15 text-blue-600 dark:text-cyan-400 border border-blue-500/30 shrink-0 hidden">
          📅 当日新增
        </span>
      </div>
      
      <!-- 顶部右侧工具集：整合“三”的穿透直达命令 + 关闭按钮 -->
      <div class="flex items-center gap-1.5 shrink-0">
        <button onclick="copyDopusCommand()" class="px-3 py-1.5 rounded-xl bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white text-xs font-bold transition-all shadow-md shadow-indigo-500/20 flex items-center gap-1.5" title="复制用于唤醒 Directory Opus 直达会话目录的命令">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
          <span class="hidden sm:inline">复制 DOpus</span>
        </button>
        <button onclick="copyExplorerCommand()" class="px-3 py-1.5 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 text-xs font-semibold border border-slate-200 dark:border-slate-700 transition-all flex items-center gap-1.5" title="复制用于唤醒 Windows 资源管理器的命令">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>
          <span class="hidden sm:inline">复制 Explorer</span>
        </button>
        <button onclick="closeDrawer()" class="text-slate-400 hover:text-slate-700 dark:hover:text-white p-1.5 rounded-xl hover:bg-slate-200 dark:hover:bg-slate-800 transition-colors ml-1" title="关闭详情">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
        </button>
      </div>
    </div>

    <!-- 抽屉主体可滚动区域 (超紧凑顶部概览 + 超宽超长开阔的对话记录) -->
    <div class="flex-1 overflow-y-auto custom-scroll p-3.5 md:p-4 space-y-3 flex flex-col min-h-0 w-full">
      
      <!-- 会话标题、数据源与归属工程项目信息 (高度紧凑精炼) -->
      <div class="p-3.5 rounded-2xl theme-subtle border space-y-1.5 shrink-0 shadow-sm">
        <div class="flex items-start justify-between gap-3">
          <div class="flex-1 min-w-0">
            <div class="text-[10px] font-bold text-slate-400 uppercase tracking-wider">会话标题</div>
            <div id="drawerTitle" class="text-sm md:text-base font-black text-slate-900 dark:text-white leading-snug break-words">-</div>
          </div>
          <div id="drawerSourceTag" class="shrink-0"></div>
        </div>

        <div class="pt-1.5 border-t theme-border flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 text-xs">
          <div class="flex items-center gap-1.5 truncate">
            <span class="text-blue-500 font-bold">📁 项目:</span>
            <span id="drawerProjectName" class="font-bold text-slate-800 dark:text-slate-200 truncate">-</span>
            <span id="drawerProjectPath" class="text-slate-400 font-mono text-[11px] truncate hidden md:inline">-</span>
          </div>
          <div class="flex items-center gap-2 shrink-0 font-mono text-[11px]">
            <span class="text-slate-400">ID: <span id="drawerCid">-</span></span>
            <button onclick="copyToClipboard(currentDetailItem.id, '会话 ID 已复制')" class="text-blue-500 hover:underline">复制</button>
          </div>
        </div>
      </div>

      <!-- 用户核心要求：“把一里面的四个合成一个小块，然后和二并列” (双栏横向并列，极大幅度压减垂直空间占用) -->
      <div class="grid grid-cols-1 xl:grid-cols-12 gap-2.5 shrink-0 w-full">
        
        <!-- 左侧【一】：4 指标 2x2 紧凑小方块 (马卡龙四色高光胶囊小方块，占据 5 列) -->
        <div class="xl:col-span-5 grid grid-cols-2 gap-2">
          <!-- 卡片 1: 对话轮数 (紫色风) -->
          <div class="p-2.5 rounded-2xl bg-purple-50/90 dark:bg-purple-950/40 border border-purple-200/80 dark:border-purple-800/50 text-center flex flex-col justify-center shadow-sm">
            <div class="text-[10px] font-bold text-purple-700 dark:text-purple-300" id="drawerTurnsTitle">💬 当日新增轮数</div>
            <div class="text-lg md:text-xl font-black text-purple-900 dark:text-purple-100 font-mono mt-0.5" id="drawerTurns">-</div>
            <div class="text-[9px] text-purple-600/80 dark:text-purple-400/80 font-mono truncate mt-0.5" id="drawerTurnsSub">提问交互</div>
          </div>
          <!-- 卡片 2: 执行步骤 (青蓝风) -->
          <div class="p-2.5 rounded-2xl bg-blue-50/90 dark:bg-blue-950/40 border border-blue-200/80 dark:border-blue-800/50 text-center flex flex-col justify-center shadow-sm">
            <div class="text-[10px] font-bold text-blue-700 dark:text-blue-300" id="drawerStepsTitle">⚡ 当日执行步骤</div>
            <div class="text-lg md:text-xl font-black text-blue-900 dark:text-blue-100 font-mono mt-0.5" id="drawerSteps">-</div>
            <div class="text-[9px] text-blue-600/80 dark:text-blue-400/80 font-mono truncate mt-0.5" id="drawerStepsSub">自主动作</div>
          </div>
          <!-- 卡片 3: 新增 Token (翡翠风) -->
          <div class="p-2.5 rounded-2xl bg-emerald-50/90 dark:bg-emerald-950/40 border border-emerald-200/80 dark:border-emerald-800/50 text-center flex flex-col justify-center shadow-sm">
            <div class="text-[10px] font-bold text-emerald-700 dark:text-emerald-300" id="drawerBilledTitle">🔥 当日净计费</div>
            <div class="text-lg md:text-xl font-black text-emerald-900 dark:text-emerald-100 font-mono mt-0.5" id="drawerNewTokens">-</div>
            <div class="text-[9px] text-emerald-600 dark:text-emerald-400 font-mono truncate mt-0.5" id="drawerBilledSub">-</div>
          </div>
          <!-- 卡片 4: 缓存命中率 (暖橙琥珀风) -->
          <div class="p-2.5 rounded-2xl bg-amber-50/90 dark:bg-amber-950/40 border border-amber-200/80 dark:border-amber-800/50 text-center flex flex-col justify-center shadow-sm">
            <div class="text-[10px] font-bold text-amber-700 dark:text-amber-300">⚡ Prompt 缓存命中</div>
            <div class="text-lg md:text-xl font-black text-amber-900 dark:text-amber-100 font-mono mt-0.5" id="drawerRate">-%</div>
            <div class="text-[9px] text-amber-600/80 dark:text-amber-400/80 font-mono truncate mt-0.5" id="drawerCacheTokens">-</div>
          </div>
        </div>

        <!-- 右侧【二】：Token 消耗构成与活跃时间分布 (占据 7 列，紧凑纵览) -->
        <div class="xl:col-span-7 p-3 rounded-2xl theme-subtle border flex flex-col justify-between space-y-2 shadow-sm">
          <div class="flex items-center justify-between text-xs font-bold text-slate-700 dark:text-slate-300">
            <span>📊 Token 消耗构成与活跃时间分布</span>
            <span class="text-[10px] font-normal text-slate-400 font-mono" id="drawerActiveDaysTag">跨度: -</span>
          </div>

          <div class="grid grid-cols-3 gap-2 text-xs">
            <div class="p-2 sm:p-2.5 rounded-xl bg-blue-50/70 dark:bg-blue-950/30 border border-blue-200/70 dark:border-blue-800/50 shadow-xs text-center flex flex-col items-center justify-center transition-all hover:scale-[1.01]">
              <div class="text-xs font-bold text-blue-700 dark:text-blue-300 tracking-wide flex items-center justify-center gap-1 mb-1">
                <span class="w-1.5 h-1.5 rounded-full bg-blue-500 shrink-0"></span>
                <span>Prompt 输入 (Input)</span>
              </div>
              <div class="font-black font-sans text-blue-600 dark:text-blue-400 text-base sm:text-lg lg:text-xl tracking-tight leading-none" id="drawerInput">-</div>
              <div class="text-[10px] sm:text-[11px] font-mono font-medium text-slate-400 dark:text-slate-400 mt-1" id="drawerInputExact">-</div>
            </div>
            <div class="p-2 sm:p-2.5 rounded-xl bg-amber-50/70 dark:bg-amber-950/30 border border-amber-200/70 dark:border-amber-800/50 shadow-xs text-center flex flex-col items-center justify-center transition-all hover:scale-[1.01]">
              <div class="text-xs font-bold text-amber-700 dark:text-amber-300 tracking-wide flex items-center justify-center gap-1 mb-1">
                <span class="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0"></span>
                <span>缓存读取 (Cache)</span>
              </div>
              <div class="font-black font-sans text-amber-500 dark:text-amber-400 text-base sm:text-lg lg:text-xl tracking-tight leading-none" id="drawerCache">-</div>
              <div class="text-[10px] sm:text-[11px] font-mono font-medium text-slate-400 dark:text-slate-400 mt-1" id="drawerCacheExact">-</div>
            </div>
            <div class="p-2 sm:p-2.5 rounded-xl bg-emerald-50/70 dark:bg-emerald-950/30 border border-emerald-200/70 dark:border-emerald-800/50 shadow-xs text-center flex flex-col items-center justify-center transition-all hover:scale-[1.01]">
              <div class="text-xs font-bold text-emerald-700 dark:text-emerald-300 tracking-wide flex items-center justify-center gap-1 mb-1">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0"></span>
                <span>思考生成 (Output)</span>
              </div>
              <div class="font-black font-sans text-emerald-600 dark:text-emerald-400 text-base sm:text-lg lg:text-xl tracking-tight leading-none" id="drawerOutput">-</div>
              <div class="text-[10px] sm:text-[11px] font-mono font-medium text-slate-400 dark:text-slate-400 mt-1" id="drawerOutputExact">-</div>
            </div>
          </div>

          <!-- 每日活跃标签流水 -->
          <div id="drawerDaysBreakdownList" class="flex flex-wrap gap-1 pt-0.5 text-[10px]">
          </div>
        </div>

      </div>

      <!-- 用户核心要求：“把下面那个对话的宽度拉大一点，更容易看到下面对话的那个” -->
      <div class="p-4 rounded-xl theme-subtle border space-y-3 flex-1 flex flex-col min-h-[440px] w-full">
        <div class="flex flex-wrap items-center justify-between gap-2 pb-2 border-b theme-border shrink-0">
          <div class="flex items-center gap-2">
            <span class="text-xs font-bold text-slate-900 dark:text-white flex items-center gap-1.5">
              <span>💬 真实提问交互逐轮记录</span>
              <span class="px-2 py-0.5 rounded-full text-[10px] bg-blue-500/15 text-blue-600 dark:text-cyan-400 font-bold" id="drawerTurnBadge">共 0 轮</span>
            </span>
          </div>
          <!-- 逐轮记录过滤器切换按钮与正倒序排序控制 -->
          <div class="flex items-center gap-2">
            <!-- 排序方向切换按钮 (默认：最新在最上) -->
            <button id="btnToggleTurnOrder" onclick="toggleDrawerTurnOrder()" class="flex items-center gap-1 px-2.5 py-1 rounded-lg border theme-border bg-slate-100/90 dark:bg-slate-800/90 hover:bg-slate-200 dark:hover:bg-slate-700 text-[11px] font-bold text-slate-700 dark:text-slate-300 transition-all shadow-xs" title="点击切换对话排序方向 (最新在最上 / 最早在最上)">
              <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16V4m0 0L3 8m4-4l4 4m6 0v12m0 0l4-4m-4 4l-4-4"/></svg>
              <span id="turnOrderText">最新在最上</span>
            </button>

            <!-- 仅看当日 / 查看全量 过滤器 -->
            <div class="flex items-center gap-1 p-0.5 rounded-lg bg-slate-200/50 dark:bg-slate-800 text-[11px]" id="drawerTurnFilterContainer">
              <button id="btnDrawerFilterScoped" onclick="setDrawerTurnsFilter(true)" class="px-2.5 py-0.5 rounded font-bold bg-blue-600 text-white shadow-sm transition-all">
                仅看当日新增
              </button>
              <button id="btnDrawerFilterAll" onclick="setDrawerTurnsFilter(false)" class="px-2.5 py-0.5 rounded text-slate-500 hover:text-slate-900 dark:hover:text-white transition-all">
                查看全量历史
              </button>
            </div>
          </div>
        </div>

        <!-- 逐轮流水列表容器 (全宽撑开、深度自适应，视觉核心区域) -->
        <div class="space-y-3 overflow-y-auto custom-scroll pr-1 flex-1 min-h-[350px] w-full" id="drawerTurnsList">
        </div>
      </div>

    </div>

    <!-- 抽屉底部 Footer (固定高度) -->
    <div class="p-4 border-t theme-border flex items-center justify-between shrink-0 bg-slate-50/50 dark:bg-slate-900/50 pl-6">
      <div class="text-[11px] text-slate-400 font-mono truncate" id="drawerFooterTime">更新时间: -</div>
      <button onclick="closeDrawer()" class="px-5 py-1.5 rounded-lg bg-slate-200 dark:bg-slate-800 hover:bg-slate-300 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-300 text-xs font-medium border theme-border">
        关闭
      </button>
    </div>
  </aside>

  <!-- 轻提示 Toast -->
  <div id="toast" class="fixed bottom-6 right-6 z-50 transform translate-y-12 opacity-0 pointer-events-none transition-all duration-300 px-4 py-2.5 rounded-lg bg-blue-600 text-white text-xs md:text-sm font-medium shadow-xl flex items-center gap-2">
    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
    <span id="toastMsg">提示内容</span>
  </div>

  <script>
    window.DASHBOARD_DATA = """ + json_summary + """;
    window.ALL_CONVERSATIONS = """ + json_conversations + """;
  </script>

  <script>
    let currentData = window.ALL_CONVERSATIONS || [];
    let filteredData = [...currentData];
    let sortField = 'user_turns'; // 默认按新增对话轮数降序排
    let sortAsc = false;
    let currentPage = 1;
    let pageSize = 15;
    let currentDetailItem = null;
    let currentTimeCategory = 'all';
    let currentSpecificDate = null; // 特定选定某天 (例如 '2026-10-14')
    let currentView = 'session'; // 'session' or 'project'
    let projectAccordionOpenState = {}; // proj_index -> boolean
    let drawerShowOnlyScoped = true; // 抽屉内是否仅看当前时间段新增轮次

    let chartInstances = {};
    let lockedTrendDate = null; // 当前在近 7 日趋势图上被固定的日期 Tooltip (锁定状态，支持鼠标自由乱动，绝不跳页)

    function formatNumber(n) { return (n || 0).toLocaleString(); }
    function formatShort(n) {
      if (!n) return '0';
      if (n >= 1e9) return (n / 1e9).toFixed(2) + ' B';
      if (n >= 1e6) return (n / 1e6).toFixed(2) + ' M';
      if (n >= 1e3) return (n / 1e3).toFixed(1) + ' K';
      return n.toString();
    }

    // ==========================================
    // 核心业务算法：获取会话在当前时间范围内的原子增量
    // 彻底解决用户指出的“新增轮数是每一天新增的，而不是总共新增的轮数”
    // ==========================================
    function getConversationStatsForContext(c) {
      const bd = c.daily_breakdown || {};
      let turns = 0, billed = 0, steps = 0, input = 0, cache = 0, output = 0;
      let hasActivity = false;

      if (currentSpecificDate) {
        if (bd[currentSpecificDate]) {
          const d = bd[currentSpecificDate];
          turns = d.user_turns || 0;
          billed = d.billed_tokens || 0;
          steps = d.step_count || 0;
          input = d.input_tokens || 0;
          cache = d.cache_read_tokens || 0;
          output = d.output_tokens || 0;
          hasActivity = (turns > 0 || billed > 0 || steps > 0);
        }
      } else if (currentTimeCategory === 'today') {
        const todayStr = window.DASHBOARD_DATA.today_str;
        if (bd[todayStr]) {
          const d = bd[todayStr];
          turns = d.user_turns || 0;
          billed = d.billed_tokens || 0;
          steps = d.step_count || 0;
          input = d.input_tokens || 0;
          cache = d.cache_read_tokens || 0;
          output = d.output_tokens || 0;
          hasActivity = (turns > 0 || billed > 0 || steps > 0);
        }
      } else if (currentTimeCategory === 'last7days') {
        const labels = (window.DASHBOARD_DATA.trend && window.DASHBOARD_DATA.trend.labels) || [];
        labels.forEach(day => {
          if (bd[day]) {
            turns += (bd[day].user_turns || 0);
            billed += (bd[day].billed_tokens || 0);
            steps += (bd[day].step_count || 0);
            input += (bd[day].input_tokens || 0);
            cache += (bd[day].cache_read_tokens || 0);
            output += (bd[day].output_tokens || 0);
          }
        });
        hasActivity = (turns > 0 || billed > 0 || steps > 0);
      } else {
        // 'all' 或其他全量历史
        turns = c.user_turns || 0;
        billed = c.billed_tokens || 0;
        steps = c.step_count || 0;
        input = c.input_tokens || 0;
        cache = c.cache_read_tokens || 0;
        output = c.output_tokens || 0;
        hasActivity = true;
      }

      const totContext = input + cache;
      const cacheRate = totContext > 0 ? ((cache / totContext) * 100).toFixed(1) : '0.0';

      return {
        turns: turns,
        billed: billed,
        steps: steps,
        input: input,
        cache: cache,
        output: output,
        cache_rate: cacheRate,
        hasActivity: hasActivity,
        total_turns: c.user_turns || 0,
        total_billed: c.billed_tokens || 0,
        total_steps: c.step_count || 0
      };
    }

    // ==========================================
    // 抽屉左侧可拖拽伸缩手柄 (Resize Splitter Handle)
    // 自由左右拉动改变抽屉宽度，并持久化到 localStorage
    // ==========================================
    function initDrawerResize() {
      const handle = document.getElementById('drawerResizeHandle');
      const drawer = document.getElementById('drawer');
      const pill = document.getElementById('drawerResizePill');
      if (!handle || !drawer) return;

      const savedW = localStorage.getItem('antigravity_drawer_width');
      if (savedW) {
        const w = parseInt(savedW);
        if (w >= 480 && w <= window.innerWidth - 40) {
          drawer.style.width = w + 'px';
        } else {
          drawer.style.width = Math.min(780, window.innerWidth - 40) + 'px';
        }
      } else {
        drawer.style.width = Math.min(780, window.innerWidth - 40) + 'px';
      }

      let isResizing = false;
      let startX = 0;
      let startW = 0;
      let hasMoved = false;

      handle.addEventListener('mousedown', (e) => {
        isResizing = true;
        hasMoved = false;
        startX = e.clientX;
        startW = drawer.getBoundingClientRect().width;
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'ew-resize';
      });

      window.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const dx = startX - e.clientX; // 鼠标向左拉则抽屉加宽
        if (Math.abs(dx) > 3) hasMoved = true;
        let newW = startW + dx;
        const minW = 480;
        const maxW = Math.min(window.innerWidth - 40, 1600);
        if (newW < minW) newW = minW;
        if (newW > maxW) newW = maxW;
        drawer.style.width = newW + 'px';
      });

      window.addEventListener('mouseup', () => {
        if (isResizing) {
          isResizing = false;
          document.body.style.userSelect = '';
          document.body.style.cursor = '';
          localStorage.setItem('antigravity_drawer_width', Math.round(drawer.getBoundingClientRect().width));
        }
      });

      // 单击按钮快速切换大屏/标准屏
      if (pill) {
        pill.addEventListener('click', (e) => {
          if (hasMoved) return; // 刚才拖动了则不触发单击切换
          const curW = drawer.getBoundingClientRect().width;
          const targetW = (curW < 960) ? Math.min(1200, window.innerWidth - 60) : 780;
          drawer.style.width = targetW + 'px';
          localStorage.setItem('antigravity_drawer_width', targetW);
          showToast(targetW > 960 ? '已展开为超宽看板' : '已恢复标准宽度看板');
        });
      }
    }

    function initTheme() {
      const savedTheme = localStorage.getItem('theme');
      // 优先让用户直接看到全新的马卡龙高饱和极光配色
      const isDark = savedTheme === 'dark';
      applyTheme(isDark);
    }

    function toggleTheme() {
      const isCurrentlyDark = document.documentElement.classList.contains('dark');
      applyTheme(!isCurrentlyDark);
    }

    function applyTheme(isDark) {
      const html = document.documentElement;
      const icon = document.getElementById('themeIcon');
      const text = document.getElementById('themeText');

      if (isDark) {
        html.classList.add('dark');
        icon.innerText = '☀️';
        text.innerText = '浅色模式';
        localStorage.setItem('theme', 'dark');
      } else {
        html.classList.remove('dark');
        icon.innerText = '🌙';
        text.innerText = '深色模式';
        localStorage.setItem('theme', 'light');
      }
      updateChartsTheme(isDark);
    }

    function updateChartsTheme(isDark) {
      const gridColor = isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.06)';
      const textColor = isDark ? '#94a3b8' : '#64748b';

      if (chartInstances.trend) {
        chartInstances.trend.options.scales.x.grid.color = gridColor;
        chartInstances.trend.options.scales.x.ticks.color = textColor;
        chartInstances.trend.options.scales.y.grid.color = gridColor;
        chartInstances.trend.options.scales.y.ticks.color = textColor;
        chartInstances.trend.update();
      }
    }

    function showToast(msg) {
      const toast = document.getElementById('toast');
      const toastMsg = document.getElementById('toastMsg');
      toastMsg.innerText = msg;
      toast.classList.remove('translate-y-12', 'opacity-0', 'pointer-events-none');
      setTimeout(() => {
        toast.classList.add('translate-y-12', 'opacity-0', 'pointer-events-none');
      }, 2500);
    }

    function copyToClipboard(text, successMsg) {
      navigator.clipboard.writeText(text).then(() => {
        showToast(successMsg || '已复制到剪贴板');
      });
    }

    function copyDopusCommand() {
      if (!currentDetailItem) return;
      const cleanPath = currentDetailItem.transcript_path.replace('/.system_generated/logs/transcript.jsonl', '').split('/').join('\\\\');
      if (location.protocol.startsWith('http')) {
        fetch('/api/open-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: currentDetailItem.transcript_path, mode: 'dopus' })
        }).then(r => r.json()).then(data => {
          showToast(data.message || '已唤醒 Directory Opus 直达');
        }).catch(() => {
          const cmd = `dopusrt.exe /cmd Go OPENCONTAINER PATH "${cleanPath}" NEWTAB=deflister,findexisting TOFRONT`;
          copyToClipboard(cmd, 'Directory Opus 唤醒命令已复制');
        });
      } else {
        const cmd = `dopusrt.exe /cmd Go OPENCONTAINER PATH "${cleanPath}" NEWTAB=deflister,findexisting TOFRONT`;
        copyToClipboard(cmd, 'Directory Opus 唤醒命令已复制');
      }
    }

    function copyExplorerCommand() {
      if (!currentDetailItem) return;
      const cleanPath = currentDetailItem.transcript_path.replace('/.system_generated/logs/transcript.jsonl', '').split('/').join('\\\\');
      if (location.protocol.startsWith('http')) {
        fetch('/api/open-folder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: currentDetailItem.transcript_path, mode: 'explorer' })
        }).then(r => r.json()).then(data => {
          showToast(data.message || '已唤醒 Windows 资源管理器');
        }).catch(() => {
          const cmd = `explorer.exe /select,"${cleanPath}"`;
          copyToClipboard(cmd, 'Explorer 资源管理器命令已复制');
        });
      } else {
        const cmd = `explorer.exe /select,"${cleanPath}"`;
        copyToClipboard(cmd, 'Explorer 资源管理器命令已复制');
      }
    }

    // ==========================================
    // 历史快照多版本回溯管理
    // ==========================================
    let currentActiveSnapshotId = 'current';
    let liveDashboardData = null;
    let liveConversationsData = null;

    function initSnapshots() {
      if (!liveDashboardData && window.DASHBOARD_DATA) {
        try {
          liveDashboardData = JSON.parse(JSON.stringify(window.DASHBOARD_DATA));
          liveConversationsData = JSON.parse(JSON.stringify(window.ALL_CONVERSATIONS || []));
        } catch (e) {
          liveDashboardData = window.DASHBOARD_DATA;
          liveConversationsData = window.ALL_CONVERSATIONS;
        }
      }

      if (location.protocol.startsWith('http')) {
        fetch('/api/snapshots')
          .then(r => r.json())
          .then(data => {
            if (data.snapshots && Array.isArray(data.snapshots)) {
              renderSnapshotOptions(data.snapshots);
            }
          })
          .catch(() => {});
      } else if (window.DASHBOARD_DATA && window.DASHBOARD_DATA.snapshots) {
        renderSnapshotOptions(window.DASHBOARD_DATA.snapshots);
      }
    }

    function renderSnapshotOptions(snapshots) {
      const select = document.getElementById('snapshotSelect');
      if (!select) return;
      const currentGenAt = (liveDashboardData && liveDashboardData.generated_at) ? liveDashboardData.generated_at : '';
      select.innerHTML = '';

      // 第一个选项：当前最新数据
      const optCurrent = document.createElement('option');
      optCurrent.value = 'current';
      optCurrent.text = `${currentGenAt || '实时数据'} (当前最新)`;
      select.appendChild(optCurrent);

      // 历史快照选项
      if (snapshots && snapshots.length > 0) {
        snapshots.forEach(s => {
          if (s.time === currentGenAt) return;
          const opt = document.createElement('option');
          opt.value = s.id;
          opt.text = `${s.time}`;
          select.appendChild(opt);
        });
      }

      select.value = currentActiveSnapshotId;
    }

    function switchSnapshot(snapId) {
      currentActiveSnapshotId = snapId;
      const ping = document.getElementById('snapshotPing');
      const dot = document.getElementById('snapshotDot');

      if (snapId === 'current') {
        if (liveDashboardData && liveConversationsData) {
          window.DASHBOARD_DATA = JSON.parse(JSON.stringify(liveDashboardData));
          window.ALL_CONVERSATIONS = JSON.parse(JSON.stringify(liveConversationsData));
          currentData = window.ALL_CONVERSATIONS;
          initKPIs();
          initCharts();
          applyFilterAndSort();
          renderProjectsView();
          if (ping) ping.classList.remove('hidden');
          if (dot) dot.className = 'relative inline-flex rounded-full h-2 w-2 bg-emerald-500';
          showToast('已切回当前最新实时数据');
        } else {
          refreshDashboardData();
        }
        return;
      }

      // 从后端读取历史快照文件
      showToast('正在加载历史快照数据...');
      fetch(`/api/snapshot?id=${encodeURIComponent(snapId)}`)
        .then(r => {
          if (!r.ok) throw new Error('快照文件读取失败');
          return r.json();
        })
        .then(snapData => {
          if (snapData.summary && snapData.conversations) {
            window.DASHBOARD_DATA = snapData.summary;
            window.ALL_CONVERSATIONS = snapData.conversations;
            currentData = window.ALL_CONVERSATIONS;
            initKPIs();
            initCharts();
            applyFilterAndSort();
            renderProjectsView();
            if (ping) ping.classList.add('hidden');
            if (dot) dot.className = 'relative inline-flex rounded-full h-2 w-2 bg-amber-500';
            showToast(`已切入历史快照: ${snapData.summary.generated_at || snapId} (只读透视)`);
          }
        })
        .catch(err => {
          showToast('加载快照失败: ' + err.message);
          const select = document.getElementById('snapshotSelect');
          if (select) select.value = 'current';
        });
    }

    function refreshDashboardData() {
      const spinner = document.getElementById('refreshSpinner');
      const text = document.getElementById('refreshText');
      if (spinner) spinner.classList.add('animate-spin');
      if (text) text.innerText = '正在扫描...';

      if (location.protocol.startsWith('http')) {
        fetch('/api/refresh', { method: 'POST' })
          .then(res => res.json())
          .then(data => {
            if (data.success && data.summary) {
              window.DASHBOARD_DATA = data.summary;
              try { liveDashboardData = JSON.parse(JSON.stringify(data.summary)); } catch(e) { liveDashboardData = data.summary; }
              currentActiveSnapshotId = 'current';

              fetch('/api/data').then(r => r.json()).then(full => {
                window.ALL_CONVERSATIONS = full.conversations || [];
                try { liveConversationsData = JSON.parse(JSON.stringify(window.ALL_CONVERSATIONS)); } catch(e) { liveConversationsData = window.ALL_CONVERSATIONS; }
                currentData = window.ALL_CONVERSATIONS;
                initKPIs();
                initCharts();
                applyFilterAndSort();
                renderProjectsView();

                if (data.snapshots) {
                  renderSnapshotOptions(data.snapshots);
                } else {
                  initSnapshots();
                }

                const ping = document.getElementById('snapshotPing');
                const dot = document.getElementById('snapshotDot');
                if (ping) ping.classList.remove('hidden');
                if (dot) dot.className = 'relative inline-flex rounded-full h-2 w-2 bg-emerald-500';

                showToast('看板数据已深度扫描更新，新快照已自动归档！');
              });
            }
          })
          .catch(err => {
            showToast('刷新服务异常，请确认 server.py 是否正在运行');
          })
          .finally(() => {
            setTimeout(() => {
              if (spinner) spinner.classList.remove('animate-spin');
              if (text) text.innerText = '重新扫描';
            }, 600);
          });
      } else {
        showToast('当前为静态文件模式，请运行 server.py 或启动批处理开启 8765 端口服务');
        if (spinner) spinner.classList.remove('animate-spin');
        if (text) text.innerText = '重新扫描';
      }
    }

    // 视角切换逻辑
    function switchView(viewName) {
      currentView = viewName;
      try { localStorage.setItem('antigravity_active_view', viewName); } catch(e) {}
      const tabSession = document.getElementById('viewTabSession');
      const tabProject = document.getElementById('viewTabProject');
      const containerSession = document.getElementById('sessionViewContainer');
      const containerProject = document.getElementById('projectViewContainer');

      const sSearch = document.getElementById('sessionSearchWrapper');
      const pSearch = document.getElementById('projectSearchWrapper');
      const btnToggleAll = document.getElementById('btnToggleAllProjects');
      const pSort = document.getElementById('projectSortWrapper');
      const sFilters = document.getElementById('sessionFilterGroup');

      if (viewName === 'session') {
        tabSession.classList.add('active');
        tabSession.classList.remove('text-slate-600', 'dark:text-slate-300');
        tabProject.classList.remove('active');
        tabProject.classList.add('text-slate-600', 'dark:text-slate-300');
        containerSession.classList.remove('hidden');
        containerProject.classList.add('hidden');

        if (sSearch) sSearch.classList.remove('hidden');
        if (pSearch) pSearch.classList.add('hidden');
        if (btnToggleAll) { btnToggleAll.classList.add('hidden'); btnToggleAll.classList.remove('flex'); }
        if (pSort) { pSort.classList.add('hidden'); pSort.classList.remove('flex'); }
        if (sFilters) { sFilters.classList.remove('hidden'); sFilters.classList.add('flex'); }
      } else {
        tabProject.classList.add('active');
        tabProject.classList.remove('text-slate-600', 'dark:text-slate-300');
        tabSession.classList.remove('active');
        tabSession.classList.add('text-slate-600', 'dark:text-slate-300');
        containerSession.classList.add('hidden');
        containerProject.classList.remove('hidden');

        if (sSearch) sSearch.classList.add('hidden');
        if (pSearch) pSearch.classList.remove('hidden');
        if (btnToggleAll) { btnToggleAll.classList.remove('hidden'); btnToggleAll.classList.add('flex'); }
        if (pSort) { pSort.classList.remove('hidden'); pSort.classList.add('flex'); }
        if (sFilters) { sFilters.classList.add('hidden'); sFilters.classList.remove('flex'); }

        renderProjectsView();
      }
    }

    function initKPIs() {
      const s = window.DASHBOARD_DATA;
      document.getElementById('kpiBilled').innerText = formatShort(s.total_billed_tokens);
      document.getElementById('kpiBilledRaw').innerText = formatNumber(s.total_billed_tokens) + ' tokens';
      
      document.getElementById('kpiCache').innerText = formatShort(s.total_cache_tokens);
      document.getElementById('kpiCacheRaw').innerText = formatNumber(s.total_cache_tokens) + ' tokens';

      // 今日卡片
      document.getElementById('kpiTodayBilled').innerText = formatShort(s.today.billed);
      document.getElementById('kpiTodayCount').innerText = `${s.today.count} 个今日会话`;
      document.getElementById('badgeTodayCount').innerText = `${s.today.count} 会话 (+${s.today.turns} 轮)`;

      // 今日新增交互量卡片 (新增轮数与新增 Token 联动)
      document.getElementById('kpiTodayTurns').innerText = '+' + formatNumber(s.today.turns);
      document.getElementById('kpiTodayNewTokens').innerText = '+' + formatShort(s.today.billed);
      document.getElementById('kpiTodaySteps').innerText = '+' + formatNumber(s.today.steps) + ' 步';
      const todayAvg = Math.round(s.today.billed / Math.max(s.today.turns, 1));
      document.getElementById('kpiTodayAvgTokens').innerText = `均 ${formatShort(todayAvg)}/轮`;

      // 全量累计交互量卡片
      document.getElementById('kpiTotalTurns').innerText = formatNumber(s.total_user_turns);
      const allAvg = Math.round(s.total_billed_tokens / Math.max(s.total_user_turns, 1));
      document.getElementById('kpiAvgTokensPerTurn').innerText = `~${formatShort(allAvg)}/轮`;
      document.getElementById('kpiTotalSteps').innerText = formatNumber(s.total_steps) + ' 步';
      document.getElementById('kpiTotalConvs').innerText = s.total_conversations;

      // 视角 Tab 徽章 (纯净大数字，极致紧凑)
      document.getElementById('badgeSessionCount').innerText = s.total_conversations;
      document.getElementById('badgeProjectCount').innerText = s.total_projects;


      document.getElementById('pieInputVal').innerText = formatShort(s.total_input_tokens);
      document.getElementById('pieCacheVal').innerText = formatShort(s.total_cache_tokens);
      document.getElementById('pieOutputVal').innerText = formatShort(s.total_output_tokens);
    }

    function initCharts() {
      const s = window.DASHBOARD_DATA;
      const isDark = document.documentElement.classList.contains('dark');
      const gridColor = isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.06)';
      const textColor = isDark ? '#94a3b8' : '#64748b';

      if (chartInstances.trend) {
        try { chartInstances.trend.destroy(); } catch (e) {}
      }
      if (chartInstances.ratio) {
        try { chartInstances.ratio.destroy(); } catch (e) {}
      }

      const ctxTrend = document.getElementById('trendChart').getContext('2d');
      chartInstances.trend = new Chart(ctxTrend, {
        type: 'line',
        data: {
          labels: s.trend.labels,
          datasets: [
            {
              label: '净计费 Token',
              data: s.trend.billed,
              borderColor: '#3b82f6',
              backgroundColor: 'rgba(59, 130, 246, 0.15)',
              tension: 0.35,
              fill: true,
              pointRadius: 4,
              pointHoverRadius: 6
            },
            {
              label: '缓存读取 Token',
              data: s.trend.cache,
              borderColor: '#fbbf24',
              backgroundColor: 'rgba(251, 191, 36, 0.05)',
              tension: 0.35,
              fill: true,
              pointRadius: 4,
              pointHoverRadius: 6
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false
          },
          onClick: (e, elements) => {
            if (e && e.native && e.native.preventDefault) {
              e.native.preventDefault();
            }
            let active = elements;
            if ((!active || active.length === 0) && chartInstances.trend) {
              active = chartInstances.trend.getElementsAtEventForMode(e, 'index', { intersect: false }, true);
            }
            if (active && active.length > 0) {
              const index = active[0].index;
              const targetDate = s.trend.labels[index];
              if (targetDate) toggleDateLock(targetDate, index);
            } else {
              // 点击了图表无数据空白区域，若当前锁定则解除锁定
              if (lockedTrendDate) unlockTrendTooltip(true);
            }
          },
          onHover: (e, elements, chart) => {
            // 当处于锁定状态时，锁定两数据集的数据点持续高亮，鼠标乱动不改变高亮
            if (lockedTrendDate) {
              const idx = s.trend.labels.indexOf(lockedTrendDate);
              if (idx >= 0) {
                chart.setActiveElements([
                  { datasetIndex: 0, index: idx },
                  { datasetIndex: 1, index: idx }
                ]);
              }
            }
          },
          onResize: (chart, size) => {
            if (lockedTrendDate) {
              const idx = s?.trend?.labels?.indexOf(lockedTrendDate);
              if (idx !== undefined && idx >= 0) {
                setTimeout(() => repositionPinnedTooltip(idx), 50);
              }
            }
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              // 关键设计：当用户锁定了卡片，禁用原生跟随鼠标乱动的 Tooltip，保持界面极致纯净！
              enabled: () => !lockedTrendDate,
              callbacks: {
                title: items => `📅 日期: ${items[0].label}`,
                label: ctx => `  ${ctx.dataset.label}: ${formatShort(ctx.raw)} (${formatNumber(ctx.raw)} tokens)`,
                afterBody: items => {
                  const billed = items.find(i => i.dataset.label === '净计费 Token')?.raw || 0;
                  const cache = items.find(i => i.dataset.label === '缓存读取 Token')?.raw || 0;
                  const tot = billed + cache;
                  const rate = tot > 0 ? ((cache / tot) * 100).toFixed(1) : 0;
                  return `\n  ───────────────\n  ✨ 当日总量: ${formatShort(tot)}\n  ⚡ 缓存命中率: ${rate}% (点击可锁定此提示框)`;
                }
              }
            }
          },
          scales: {
            x: { grid: { color: gridColor }, ticks: { color: textColor } },
            y: { 
              grid: { color: gridColor }, 
              ticks: { color: textColor, callback: val => formatShort(val) } 
            }
          }
        }
      });

      // 动态渲染近 7 日每日具体用量速览矩阵
      const pillsContainer = document.getElementById('dailyTrendPills');
      if (pillsContainer && s.trend && s.trend.labels) {
        pillsContainer.innerHTML = '';
        s.trend.labels.forEach((dayStr, idx) => {
          const billed = s.trend.billed[idx] || 0;
          const cache = s.trend.cache[idx] || 0;
          const shortDate = dayStr.slice(5);
          const pill = document.createElement('div');
          pill.setAttribute('data-date', dayStr);
          pill.className = 'daily-preview-pill p-2 rounded-xl theme-subtle border hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-all cursor-pointer group select-none';
          pill.onclick = (e) => {
            if (e) {
              if (e.preventDefault) e.preventDefault();
              if (e.stopPropagation) e.stopPropagation();
            }
            toggleDateLock(dayStr, idx);
          };
          pill.title = `点击锁定/筛选 ${dayStr} 会话明细\n净计费: ${formatNumber(billed)}\n缓存读取: ${formatNumber(cache)}`;
          pill.innerHTML = `
            <div class="text-[11px] font-mono font-semibold text-slate-500 dark:text-slate-400 group-hover:text-cyan-500 transition-colors">${shortDate}</div>
            <div class="text-xs font-extrabold text-blue-600 dark:text-cyan-400 font-mono mt-0.5">${formatShort(billed)}</div>
            <div class="text-[10px] text-amber-500 font-mono mt-0.5 truncate" title="缓存读取: ${formatShort(cache)}">${formatShort(cache)}⚡</div>
          `;
          pillsContainer.appendChild(pill);
        });
      }

      // 若已有锁定日期，在重新渲染图表后恢复锁定框
      if (lockedTrendDate) {
        const idx = s.trend.labels.indexOf(lockedTrendDate);
        if (idx >= 0) {
          setTimeout(() => {
            renderAndPositionPinnedTooltip(lockedTrendDate, idx);
            updateDailyPillsHighlight(lockedTrendDate);
          }, 60);
        } else {
          unlockTrendTooltip(false);
        }
      }

      const ctxRatio = document.getElementById('ratioChart').getContext('2d');
      chartInstances.ratio = new Chart(ctxRatio, {
        type: 'doughnut',
        data: {
          labels: ['新增输入', '缓存读取', '模型生成'],
          datasets: [{
            data: [s.total_input_tokens, s.total_cache_tokens, s.total_output_tokens],
            backgroundColor: ['#3b82f6', '#fbbf24', '#10b981'],
            borderWidth: 0
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '72%',
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: ctx => `${ctx.label}: ${formatShort(ctx.raw)} (${((ctx.raw / s.total_context_tokens)*100).toFixed(1)}%)`
              }
            }
          }
        }
      });
    }

    // ==========================================
    // 图表提示框“一键锁定/防跳页”核心控制器 (Lockable Pinned Tooltip)
    // 彻底解决用户指出的：“点击日期不要跳转到下面，锁定框后鼠标可以乱动”
    // ==========================================

    function lockTrendTooltip(targetDate, targetIndex) {
      const savedScrollY = window.scrollY;
      const s = window.DASHBOARD_DATA;
      if (!s || !s.trend || !s.trend.labels) return;

      let idx = targetIndex;
      if (idx === undefined || idx === null || idx < 0) {
        idx = s.trend.labels.indexOf(targetDate);
      }
      if (idx < 0) return;

      lockedTrendDate = targetDate;

      // 1. 同步执行底层数据筛选与徽章更新（静默生效，绝不调用 scrollIntoView 跳页，严格保全视口）
      filterBySpecificDate(targetDate, false);

      // 2. 渲染并定位锁定的 Tooltip 浮层卡片
      renderAndPositionPinnedTooltip(targetDate, idx);

      // 3. 高亮图表数据点
      if (chartInstances.trend) {
        try {
          chartInstances.trend.setActiveElements([
            { datasetIndex: 0, index: idx },
            { datasetIndex: 1, index: idx }
          ]);
          chartInstances.trend.update();
        } catch (e) {}
      }

      // 4. 更新下方速览胶囊高亮发光状态
      updateDailyPillsHighlight(targetDate);

      // 【视口锁定铁律】：严格保持当前滚动坐标，绝不跳页
      window.scrollTo({ top: savedScrollY, behavior: 'instant' });

      showToast(`已锁定 ${targetDate} 数据卡片 (鼠标可乱动)`);
    }

    function unlockTrendTooltip(needClearFilter = true) {
      const savedScrollY = window.scrollY;
      if (!lockedTrendDate) return;
      lockedTrendDate = null;

      // 1. 隐藏锁定浮层
      const tooltipElem = document.getElementById('trendChartPinnedTooltip');
      if (tooltipElem) tooltipElem.classList.add('hidden');

      // 2. 清除图表点高亮
      if (chartInstances.trend) {
        try {
          chartInstances.trend.setActiveElements([]);
          chartInstances.trend.update();
        } catch (e) {}
      }

      // 3. 清除下方胶囊高亮
      updateDailyPillsHighlight(null);

      // 4. 恢复全部数据筛选
      if (needClearFilter && currentSpecificDate) {
        clearSpecificDate(false);
      }
      window.scrollTo({ top: savedScrollY, behavior: 'instant' });
    }

    function toggleDateLock(targetDate, targetIndex) {
      const savedScrollY = window.scrollY;
      if (lockedTrendDate === targetDate) {
        unlockTrendTooltip(true);
      } else {
        lockTrendTooltip(targetDate, targetIndex);
      }
      window.scrollTo({ top: savedScrollY, behavior: 'instant' });
    }

    function updateDailyPillsHighlight(activeDate) {
      const pillsContainer = document.getElementById('dailyTrendPills');
      if (!pillsContainer) return;
      const pills = pillsContainer.querySelectorAll('.daily-preview-pill');
      pills.forEach(pill => {
        const d = pill.getAttribute('data-date');
        if (activeDate && d === activeDate) {
          pill.classList.add('border-cyan-500', 'ring-2', 'ring-cyan-500/40', 'bg-cyan-500/10');
        } else {
          pill.classList.remove('border-cyan-500', 'ring-2', 'ring-cyan-500/40', 'bg-cyan-500/10');
        }
      });
    }

    function renderAndPositionPinnedTooltip(targetDate, idx) {
      const s = window.DASHBOARD_DATA;
      const container = document.getElementById('trendChartContainer');
      const tooltipElem = document.getElementById('trendChartPinnedTooltip');
      if (!s || !container || !tooltipElem || !chartInstances.trend) return;

      const billed = s.trend.billed[idx] || 0;
      const cache = s.trend.cache[idx] || 0;
      const tot = billed + cache;
      const rate = tot > 0 ? ((cache / tot) * 100).toFixed(1) : 0;

      // 精致的深色高对比度玻璃拟态卡片，1:1 还原并超越原生 Tooltip 质感
      tooltipElem.innerHTML = `
        <div class="bg-slate-900/95 dark:bg-slate-950/95 text-white backdrop-blur-md px-3.5 py-2.5 rounded-xl border border-slate-700/80 shadow-2xl text-[12px] min-w-[260px] max-w-[320px] leading-relaxed">
          <div class="flex items-center justify-between pb-1.5 border-b border-slate-700/60 mb-2">
            <div class="flex items-center gap-1.5 font-bold text-slate-100 font-mono">
              <span>📅</span>
              <span>日期: ${targetDate}</span>
            </div>
            <div class="flex items-center gap-1.5">
              <span class="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-cyan-500/20 text-cyan-400 border border-cyan-500/30">
                🔒 已锁定
              </span>
              <button onclick="unlockTrendTooltip()" class="text-slate-400 hover:text-white hover:bg-slate-800 p-0.5 rounded transition-colors text-xs font-bold leading-none cursor-pointer" title="点击解锁 / 关闭">✕</button>
            </div>
          </div>
          <div class="space-y-1 font-mono text-[11px]">
            <div class="flex items-center justify-between gap-3">
              <span class="text-slate-300 flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-sm bg-blue-500 inline-block"></span>
                净计费 Token:
              </span>
              <span class="font-bold text-blue-400">${formatShort(billed)} <span class="text-[10px] text-slate-400 font-normal">(${formatNumber(billed)})</span></span>
            </div>
            <div class="flex items-center justify-between gap-3">
              <span class="text-slate-300 flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-sm bg-amber-400 inline-block"></span>
                缓存读取 Token:
              </span>
              <span class="font-bold text-amber-300">${formatShort(cache)} <span class="text-[10px] text-slate-400 font-normal">(${formatNumber(cache)})</span></span>
            </div>
          </div>
          <div class="my-2 border-t border-slate-700/60"></div>
          <div class="space-y-1 text-[11px]">
            <div class="flex items-center justify-between text-slate-300">
              <span class="flex items-center gap-1">✨ 当日总量:</span>
              <span class="font-bold font-mono text-white">${formatShort(tot)}</span>
            </div>
            <div class="flex items-center justify-between text-slate-300">
              <span class="flex items-center gap-1">⚡ 缓存命中率:</span>
              <span class="font-bold font-mono text-emerald-400">${rate}%</span>
            </div>
          </div>
          <div class="mt-2 pt-1.5 border-t border-slate-800/80 text-[10px] text-slate-400 flex items-center justify-between select-none">
            <span class="text-slate-400">💡 提示框已锁定，鼠标可乱动</span>
            <span class="text-cyan-400/90 hover:text-cyan-300 cursor-pointer font-medium" onclick="unlockTrendTooltip()">点击解锁 ✕</span>
          </div>
        </div>
      `;

      // 阻止浮层内部点击冒泡（允许选中文本复制，不误触 canvas 解锁）
      tooltipElem.onclick = (e) => e.stopPropagation();

      // 显示浮层
      tooltipElem.classList.remove('hidden');

      // 计算像素定位
      repositionPinnedTooltip(idx);
    }

    function repositionPinnedTooltip(idx) {
      const container = document.getElementById('trendChartContainer');
      const tooltipElem = document.getElementById('trendChartPinnedTooltip');
      if (!container || !tooltipElem || !chartInstances.trend) return;

      const meta0 = chartInstances.trend.getDatasetMeta(0);
      const meta1 = chartInstances.trend.getDatasetMeta(1);
      if (!meta0 || !meta0.data || !meta0.data[idx]) return;

      const p0 = meta0.data[idx];
      const p1 = meta1 && meta1.data ? meta1.data[idx] : null;

      const anchorX = p0.x;
      const anchorY = p1 ? Math.min(p0.y, p1.y) : p0.y;

      const containerWidth = container.clientWidth;
      const containerHeight = container.clientHeight;

      let transformX = '-50%';
      let left = anchorX;

      if (anchorX < 140) {
        transformX = '0%';
        left = Math.max(8, anchorX - 16);
      } else if (anchorX > containerWidth - 140) {
        transformX = '-100%';
        left = Math.min(containerWidth - 8, anchorX + 16);
      }

      let transformY = '-100%';
      let top = anchorY - 12;

      // 如果数据点靠近容器顶部，上方空间不足（容器高 240px，卡片高约 165px），则放置在下方
      if (anchorY < 165) {
        transformY = '0%';
        top = anchorY + 14;
      }

      tooltipElem.style.left = `${left}px`;
      tooltipElem.style.top = `${top}px`;
      tooltipElem.style.transform = `translate(${transformX}, ${transformY})`;
    }

    function filterBySpecificDate(dateStr, showToastMsg = true) {
      const savedScrollY = window.scrollY; // 锁死当前页面垂直滚动位置

      currentSpecificDate = dateStr;
      currentTimeCategory = 'all';

      // 清空文本搜索框，避免污染模糊搜索
      const searchInput = document.getElementById('searchInput');
      if (searchInput) searchInput.value = '';
      const projSearchInput = document.getElementById('projectSearchInput');
      if (projSearchInput) projSearchInput.value = '';

      // 取消常规分类胶囊的高亮
      ['all', 'today', 'last7days', 'older'].forEach(cat => {
        const btnSession = document.getElementById('pill-' + cat);
        const btnProj = document.getElementById('proj-pill-' + cat);
        if (btnSession) btnSession.classList.remove('active');
        if (btnProj) btnProj.classList.remove('active');
      });

      updateSpecificDateBadge();
      applyFilterAndSort();
      renderProjectsView();

      // 【视口锁定铁律】：严格保持当前滚动坐标，绝不跳页，确保用户视角稳稳停留在图表区域
      window.scrollTo({ top: savedScrollY, behavior: 'instant' });

      if (showToastMsg) {
        showToast(`已为您精准锁定 ${dateStr} 的记录`);
      }
    }

    function clearSpecificDate(unlockChart = true) {
      const savedScrollY = window.scrollY;
      currentSpecificDate = null;
      setTimeFilter('all');
      if (unlockChart && lockedTrendDate) {
        unlockTrendTooltip(false);
      }
      window.scrollTo({ top: savedScrollY, behavior: 'instant' });
      showToast('已恢复查看全部时间记录');
    }

    function updateSpecificDateBadge() {
      const badge = document.getElementById('globalSpecificDateBadge');
      const text = document.getElementById('globalSpecificDateText');

      if (!badge) return;
      if (currentSpecificDate) {
        badge.classList.remove('hidden');
        badge.classList.add('flex');
        if (text) text.innerText = `📅 已限定日期: ${currentSpecificDate}`;
      } else {
        badge.classList.add('hidden');
        badge.classList.remove('flex');
      }
    }

    function setTimeFilter(category) {
      const savedScrollY = window.scrollY;
      currentTimeCategory = category;
      currentSpecificDate = null; // 清除特定单日限定
      updateSpecificDateBadge();

      ['all', 'today', 'last7days', 'older'].forEach(cat => {
        const btnSession = document.getElementById('pill-' + cat);
        const btnProj = document.getElementById('proj-pill-' + cat);
        if (cat === category) {
          if (btnSession) { btnSession.classList.add('active'); btnSession.classList.remove('text-slate-600', 'dark:text-slate-300'); }
          if (btnProj) { btnProj.classList.add('active'); btnProj.classList.remove('text-slate-600', 'dark:text-slate-300'); }
        } else {
          if (btnSession) { btnSession.classList.remove('active'); btnSession.classList.add('text-slate-600', 'dark:text-slate-300'); }
          if (btnProj) { btnProj.classList.remove('active'); btnProj.classList.add('text-slate-600', 'dark:text-slate-300'); }
        }
      });

      applyFilterAndSort();
      renderProjectsView();
      window.scrollTo({ top: savedScrollY, behavior: 'instant' });
    }

    function applyFilterAndSort() {
      const q = (document.getElementById('searchInput').value || '').trim().toLowerCase();
      const level = document.getElementById('filterLevel').value;
      const source = document.getElementById('filterSource').value;

      filteredData = currentData.filter(d => {
        // 挂载动态上下文统计指标
        d._ctx = getConversationStatsForContext(d);

        // 1. 若处于特定日期或今日限定，仅保留在该时间范围内有增量活动 (turns > 0 或 billed > 0 或 steps > 0) 的会话
        if (currentSpecificDate || currentTimeCategory === 'today' || currentTimeCategory === 'last7days') {
          if (!d._ctx.hasActivity) return false;
        } else if (currentTimeCategory === 'older') {
          if (d.time_category !== 'older') return false;
        }

        // 2. 搜索匹配：包含标题、ID、项目名、项目路径、以及日期字符串！
        const matchQ = !q || 
                       d.title.toLowerCase().includes(q) || 
                       d.id.toLowerCase().includes(q) || 
                       (d.project_name && d.project_name.toLowerCase().includes(q)) ||
                       (d.project_path && d.project_path.toLowerCase().includes(q)) ||
                       (d.day && d.day.toLowerCase().includes(q)) ||
                       (d.last_modified_time && d.last_modified_time.toLowerCase().includes(q));
        if (!matchQ) return false;

        if (source === 'official' && !d.is_official) return false;
        if (source === 'estimated' && !d.is_estimated) return false;

        const checkBilled = d._ctx.billed;
        if (level === 'high') return checkBilled >= 3000000;
        if (level === 'mid') return checkBilled >= 500000 && checkBilled < 3000000;
        if (level === 'low') return checkBilled < 500000;

        return true;
      });

      filteredData.sort((a, b) => {
        let va, vb;
        if (sortField === 'user_turns') {
          va = a._ctx.turns; vb = b._ctx.turns;
        } else if (sortField === 'billed_tokens') {
          va = a._ctx.billed; vb = b._ctx.billed;
        } else if (sortField === 'step_count') {
          va = a._ctx.steps; vb = b._ctx.steps;
        } else if (sortField === 'cache_read_tokens') {
          va = a._ctx.cache; vb = b._ctx.cache;
        } else if (sortField === 'input_tokens') {
          va = a._ctx.input; vb = b._ctx.input;
        } else if (sortField === 'output_tokens') {
          va = a._ctx.output; vb = b._ctx.output;
        } else {
          va = a[sortField]; vb = b[sortField];
        }

        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAsc ? -1 : 1;
        if (va > vb) return sortAsc ? 1 : -1;
        return 0;
      });

      if (document.getElementById('filteredCountTag')) document.getElementById('filteredCountTag').innerText = filteredData.length;
      const badgeSession = document.getElementById('badgeSessionCount');
      if (badgeSession) badgeSession.innerText = filteredData.length;
      updateSummaryRow();
      currentPage = 1;
      renderTable();
    }

    function updateSummaryRow() {
      const count = filteredData.length;
      // 真实汇总当前筛选范围内的原子增量
      const totalBilled = filteredData.reduce((acc, d) => acc + (d._ctx ? d._ctx.billed : d.billed_tokens), 0);
      const totalIn = filteredData.reduce((acc, d) => acc + (d._ctx ? d._ctx.input : d.input_tokens), 0);
      const totalCache = filteredData.reduce((acc, d) => acc + (d._ctx ? d._ctx.cache : d.cache_read_tokens), 0);
      const totalOut = filteredData.reduce((acc, d) => acc + (d._ctx ? d._ctx.output : d.output_tokens), 0);
      const totalTurns = filteredData.reduce((acc, d) => acc + (d._ctx ? d._ctx.turns : d.user_turns), 0);
      const totalSteps = filteredData.reduce((acc, d) => acc + (d._ctx ? d._ctx.steps : d.step_count), 0);

      let prefix = '当前筛选';
      if (currentSpecificDate) prefix = `📅 ${currentSpecificDate} 当日新增`;
      else if (currentTimeCategory === 'today') prefix = '📅 今日 / 近 24 小时新增';
      else if (currentTimeCategory === 'last7days') prefix = '近 7 日新增';
      else if (currentTimeCategory === 'older') prefix = '更早历史';

      document.getElementById('summaryTimePrefixText').innerText = prefix;
      document.getElementById('summaryTotalCount').innerText = count;
      document.getElementById('summaryBilled').innerText = formatShort(totalBilled);
      document.getElementById('summaryBilled').title = formatNumber(totalBilled) + ' tokens';
      document.getElementById('summaryTurns').innerText = formatNumber(totalTurns) + ' 轮';
      document.getElementById('summarySteps').innerText = formatNumber(totalSteps) + ' 步';
      document.getElementById('summaryCache').innerText = formatShort(totalCache);
      document.getElementById('summaryInput').innerText = formatShort(totalIn);
      document.getElementById('summaryOutput').innerText = formatShort(totalOut);
    }

    function changeSort(field) {
      if (sortField === field) sortAsc = !sortAsc;
      else { sortField = field; sortAsc = false; }
      updateSortIcons();
      applyFilterAndSort();
    }

    function updateSortIcons() {
      ['title', 'billed_tokens', 'user_turns', 'step_count', 'cache_read_tokens', 'input_tokens', 'output_tokens', 'last_modified_time'].forEach(f => {
        const el = document.getElementById('sort_' + f);
        if (el) {
          if (sortField === f) {
            el.innerText = sortAsc ? '▲' : '▼';
            el.className = 'text-xs text-blue-600 dark:text-cyan-400 font-bold';
          } else el.innerText = '';
        }
      });
    }

    function renderTable() {
      const tbody = document.getElementById('tableBody');
      tbody.innerHTML = '';

      const totalItems = filteredData.length;
      const actualSize = pageSize === 'all' ? totalItems : parseInt(pageSize);
      const totalPages = Math.ceil(totalItems / actualSize) || 1;
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * actualSize;
      const endIndex = pageSize === 'all' ? totalItems : Math.min(startIndex + actualSize, totalItems);
      const pageItems = filteredData.slice(startIndex, endIndex);

      const hasTimeScope = Boolean(currentSpecificDate || currentTimeCategory === 'today' || currentTimeCategory === 'last7days');

      if (pageItems.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="py-12 text-center text-slate-400 dark:text-slate-500 whitespace-nowrap">未找到符合条件的会话记录</td></tr>`;
      } else {
        pageItems.forEach((d, idx) => {
          const tr = document.createElement('tr');
          tr.className = 'hover:bg-slate-100 dark:hover:bg-slate-800/50 transition-colors cursor-pointer group whitespace-nowrap';
          tr.onclick = (e) => {
            if (!e.target.closest('button')) openDrawer(d);
          };

          const stats = d._ctx || getConversationStatsForContext(d);

          let badge = '';
          if (stats.billed >= 3000000) {
            badge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold badge-high ml-2 whitespace-nowrap">重度</span>';
          } else if (stats.billed >= 500000) {
            badge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold badge-mid ml-2 whitespace-nowrap">中度</span>';
          }

          let estBadge = '';
          if (d.is_estimated) {
            estBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-semibold badge-est ml-1.5 whitespace-nowrap" title="早期日志未含官方统计字段，此为思维链字符级 NLP 逆向推导估算值">历史估算</span>';
          }

          const prefix = d.is_estimated ? '~' : '';

          // 核心展示优化：当日新增与历史累计对照
          const turnSub = (hasTimeScope && stats.turns !== stats.total_turns)
            ? `<div class="text-[10px] text-slate-400 font-mono scale-90 -mt-0.5">累计 ${stats.total_turns} 轮</div>`
            : '';
          const billedSub = (hasTimeScope && stats.billed !== stats.total_billed)
            ? `<div class="text-[10px] text-slate-400 font-mono scale-90 -mt-0.5">累计 ${formatShort(stats.total_billed)}</div>`
            : '';
          const stepSub = (hasTimeScope && stats.steps !== stats.total_steps)
            ? `<div class="text-[10px] text-slate-400 font-mono scale-90 -mt-0.5">累计 ${formatNumber(stats.total_steps)} 步</div>`
            : '';

          tr.innerHTML = `
            <td class="py-3 px-4 whitespace-nowrap">
              <div class="font-medium text-slate-900 dark:text-white group-hover:text-blue-600 dark:group-hover:text-cyan-400 transition-colors flex items-center flex-nowrap whitespace-nowrap">
                <span class="truncate max-w-xs md:max-w-md font-semibold">${d.title}</span>
                ${badge}
                ${estBadge}
              </div>
              <div class="text-[11px] text-slate-400 font-mono mt-0.5 flex items-center gap-2 whitespace-nowrap">
                <span>${d.id.slice(0, 8)}...</span>
                <span class="truncate max-w-[240px] text-blue-500/80 dark:text-cyan-400/80 font-medium" title="${d.project_path}">📁 ${d.project_name}</span>
              </div>
            </td>
            
            <!-- 核心列 1: 新增 Token (醒目加粗) -->
            <td class="py-3 px-4 text-right whitespace-nowrap">
              <div class="font-extrabold text-blue-600 dark:text-cyan-400 font-mono text-sm">${prefix}${formatShort(stats.billed)}</div>
              ${billedSub}
            </td>
            
            <!-- 核心列 2: 新增对话轮数 (蓝色徽章) -->
            <td class="py-3 px-4 text-center whitespace-nowrap">
              <div class="inline-flex flex-col items-center">
                <span class="inline-flex items-center px-3 py-0.5 rounded-full text-xs font-extrabold bg-blue-500/15 text-blue-600 dark:text-cyan-400 font-mono border border-blue-500/30 shadow-sm whitespace-nowrap">
                  ${stats.turns} 轮
                </span>
                ${turnSub}
              </div>
            </td>

            <!-- 核心列 3: 执行步骤数 -->
            <td class="py-3 px-4 text-center whitespace-nowrap">
              <div class="font-mono text-xs text-slate-700 dark:text-slate-300 font-medium">${formatNumber(stats.steps)} 步</div>
              ${stepSub}
            </td>
            
            <!-- 辅助列: 缓存命中 -->
            <td class="py-3 px-4 text-right text-amber-600 dark:text-amber-400 font-mono font-medium whitespace-nowrap">${prefix}${formatShort(stats.cache)}</td>
            <!-- 辅助列: Prompt 输入 -->
            <td class="py-3 px-4 text-right text-blue-500 font-mono whitespace-nowrap">${prefix}${formatShort(stats.input)}</td>
            <!-- 辅助列: 生成输出 -->
            <td class="py-3 px-4 text-right text-emerald-600 dark:text-emerald-400 font-mono whitespace-nowrap">${prefix}${formatShort(stats.output)}</td>
            
            <td class="py-3 px-4 text-right text-slate-500 text-xs whitespace-nowrap">${d.last_modified_time.slice(0, 16) || '-'}</td>
            <td class="py-3 px-4 text-center whitespace-nowrap">
              <button onclick="openDrawer(filteredData[${startIndex + idx}])" class="px-2.5 py-1 rounded text-xs bg-slate-200 dark:bg-slate-800 hover:bg-blue-600 hover:text-white dark:hover:bg-cyan-500 text-slate-700 dark:text-slate-300 transition-all border theme-border font-medium whitespace-nowrap">
                查看
              </button>
            </td>
          `;
          tbody.appendChild(tr);
        });
      }

      const pageInfo = document.getElementById('pageInfoTag');
      pageInfo.innerText = totalItems === 0 
        ? '没有记录' 
        : `正在展示第 ${startIndex + 1} 至 ${endIndex} 条，共 ${totalItems} 条`;

      renderPagination(totalPages);
    }

    function renderPagination(totalPages) {
      const controls = document.getElementById('paginationControls');
      controls.innerHTML = '';
      if (totalPages <= 1) return;

      const prevBtn = document.createElement('button');
      prevBtn.className = `px-2.5 py-1 text-xs rounded border theme-border ${currentPage === 1 ? 'opacity-40 cursor-not-allowed' : 'hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-600 dark:text-slate-300'}`;
      prevBtn.innerText = '‹ 上一页';
      prevBtn.disabled = currentPage === 1;
      prevBtn.onclick = () => { if (currentPage > 1) { currentPage--; renderTable(); } };
      controls.appendChild(prevBtn);

      const maxPagesToShow = 5;
      let startP = Math.max(1, currentPage - 2);
      let endP = Math.min(totalPages, startP + maxPagesToShow - 1);
      if (endP - startP < maxPagesToShow - 1) {
        startP = Math.max(1, endP - maxPagesToShow + 1);
      }

      for (let p = startP; p <= endP; p++) {
        const pBtn = document.createElement('button');
        pBtn.className = `w-7 h-7 text-xs rounded border ${p === currentPage ? 'bg-blue-600 dark:bg-cyan-500 text-white font-bold border-transparent' : 'theme-border text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-800'}`;
        pBtn.innerText = p;
        pBtn.onclick = () => { currentPage = p; renderTable(); };
        controls.appendChild(pBtn);
      }

      const nextBtn = document.createElement('button');
      nextBtn.className = `px-2.5 py-1 text-xs rounded border theme-border ${currentPage === totalPages ? 'opacity-40 cursor-not-allowed' : 'hover:bg-slate-200 dark:hover:bg-slate-800 text-slate-600 dark:text-slate-300'}`;
      nextBtn.innerText = '下一页 ›';
      nextBtn.disabled = currentPage === totalPages;
      nextBtn.onclick = () => { if (currentPage < totalPages) { currentPage++; renderTable(); } };
      controls.appendChild(nextBtn);
    }

    // ==========================================
    // 按项目透视视图渲染逻辑 (Project Perspective)
    // 项目内所有指标均基于当前选定日期原子增量核算与汇总
    // ==========================================
    function renderProjectsView() {
      const container = document.getElementById('projectsAccordionList');
      if (!container) return;
      container.innerHTML = '';

      const query = (document.getElementById('projectSearchInput').value || '').trim().toLowerCase();
      const sortBy = (document.getElementById('projectSortSelect') ? document.getElementById('projectSortSelect').value : 'turns');
      const allProjects = window.DASHBOARD_DATA.projects || [];
      const hasTimeScope = Boolean(currentSpecificDate || currentTimeCategory === 'today' || currentTimeCategory === 'last7days');

      let filteredProjects = [];

      allProjects.forEach(proj => {
        // 对项目内会话进行时间增量判定与过滤
        let matchingConvs = [];
        (proj.conversations || []).forEach(c => {
          c._ctx = getConversationStatsForContext(c);

          if (hasTimeScope) {
            if (!c._ctx.hasActivity) return;
          } else if (currentTimeCategory === 'older') {
            if (c.time_category !== 'older') return;
          }

          // 关键字过滤
          if (query) {
            const matchProj = (proj.name && proj.name.toLowerCase().includes(query)) || 
                              (proj.path && proj.path.toLowerCase().includes(query));
            if (!matchProj) {
              const matchC = (c.title && c.title.toLowerCase().includes(query)) || (c.id && c.id.toLowerCase().includes(query));
              if (!matchC) return;
            }
          }

          matchingConvs.push(c);
        });

        if (matchingConvs.length === 0) return;

        // 项目内子会话默认按当前时间段新增轮数降序排
        matchingConvs.sort((a, b) => {
          if (b._ctx.turns !== a._ctx.turns) return b._ctx.turns - a._ctx.turns;
          return b._ctx.billed - a._ctx.billed;
        });

        // 动态汇总该项目在当前选定时间段内的原子指标
        const dynamicBilled = matchingConvs.reduce((acc, c) => acc + c._ctx.billed, 0);
        const dynamicTurns = matchingConvs.reduce((acc, c) => acc + c._ctx.turns, 0);
        const dynamicSteps = matchingConvs.reduce((acc, c) => acc + c._ctx.steps, 0);
        const dynamicCache = matchingConvs.reduce((acc, c) => acc + c._ctx.cache, 0);
        const dynamicInput = matchingConvs.reduce((acc, c) => acc + c._ctx.input, 0);
        const totInCache = dynamicInput + dynamicCache;
        const dynamicCacheRate = totInCache > 0 ? ((dynamicCache / totInCache) * 100).toFixed(1) : '0.0';
        const dynamicLmt = matchingConvs.reduce((max, c) => ((c.last_modified_time || '') > max ? (c.last_modified_time || '') : max), '');

        filteredProjects.push({
          name: proj.name,
          path: proj.path,
          conv_count: matchingConvs.length,
          billed_tokens: dynamicBilled,
          user_turns: dynamicTurns,
          step_count: dynamicSteps,
          cache_read_tokens: dynamicCache,
          cache_hit_rate: dynamicCacheRate,
          last_modified_time: dynamicLmt,
          conversations: matchingConvs
        });
      });

      // 项目卡片整体排序（默认 turns 降序）
      filteredProjects.sort((a, b) => {
        if (sortBy === 'billed') return b.billed_tokens - a.billed_tokens;
        if (sortBy === 'convs') return b.conv_count - a.conv_count;
        if (sortBy === 'turns') return b.user_turns - a.user_turns;
        if (sortBy === 'time') return (b.last_modified_time || '').localeCompare(a.last_modified_time || '');
        return 0;
      });

      const badgeProj = document.getElementById('badgeProjectCount');
      if (badgeProj) {
        badgeProj.innerText = filteredProjects.length;
      }

      if (filteredProjects.length === 0) {
        container.innerHTML = `<div class="theme-card rounded-2xl p-12 text-center text-slate-400">
          <div class="text-3xl mb-2">🔍</div>
          <div class="text-sm font-semibold">当前所选时间段或检索条件下未找到任何项目</div>
          <div class="text-xs text-slate-500 mt-1">请尝试切换上方时间胶囊（如“全部时间”）或清空搜索关键字</div>
        </div>`;
        return;
      }

      filteredProjects.forEach((proj, pIdx) => {
        if (projectAccordionOpenState[proj.name] === undefined) {
          projectAccordionOpenState[proj.name] = (pIdx < 3);
        }
        const isOpen = projectAccordionOpenState[proj.name];

        const card = document.createElement('div');
        card.className = 'project-card rounded-2xl border theme-border overflow-hidden bg-white/70 dark:bg-slate-900/40 hover:border-indigo-500/30 transition-all shadow-sm';
        card.id = `proj-card-${pIdx}`;
        card.dataset.projName = proj.name;

        let convRowsHtml = '';
        proj.conversations.forEach((c) => {
          const stats = c._ctx || getConversationStatsForContext(c);
          const turnSub = (hasTimeScope && stats.turns !== stats.total_turns)
            ? `<div class="text-[9px] text-slate-400 font-mono scale-90">累计 ${stats.total_turns} 轮</div>`
            : '';
          const billedSub = (hasTimeScope && stats.billed !== stats.total_billed)
            ? `<div class="text-[9px] text-slate-400 font-mono scale-90">累计 ${formatShort(stats.total_billed)}</div>`
            : '';

          convRowsHtml += `
            <tr class="hover:bg-slate-100 dark:hover:bg-slate-800/60 transition-colors cursor-pointer group whitespace-nowrap" onclick="openDrawerByCid('${c.id}')">
              <td class="py-2.5 px-3 whitespace-nowrap">
                <div class="font-medium text-slate-900 dark:text-white group-hover:text-blue-500 dark:group-hover:text-cyan-400 transition-colors flex items-center flex-nowrap whitespace-nowrap">
                  <span class="truncate max-w-sm md:max-w-lg font-semibold">${c.title}</span>
                  ${c.is_estimated ? '<span class="px-1.5 py-0.2 rounded text-[10px] font-semibold badge-est ml-1.5 whitespace-nowrap">估算</span>' : ''}
                </div>
                <div class="text-[10px] text-slate-400 font-mono mt-0.5 whitespace-nowrap">
                  ID: ${c.id.slice(0, 8)}... | 更新于: ${c.last_modified_time.slice(0, 16) || '-'}
                </div>
              </td>
              <td class="py-2.5 px-3 text-right whitespace-nowrap">
                <div class="font-extrabold text-blue-600 dark:text-cyan-400 font-mono text-xs">${formatShort(stats.billed)}</div>
                ${billedSub}
              </td>
              <td class="py-2.5 px-3 text-center whitespace-nowrap">
                <div class="inline-flex flex-col items-center">
                  <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-bold bg-blue-500/15 text-blue-600 dark:text-cyan-400 font-mono border border-blue-500/20 whitespace-nowrap">
                    ${stats.turns} 轮
                  </span>
                  ${turnSub}
                </div>
              </td>
              <td class="py-2.5 px-3 text-center text-xs text-slate-700 dark:text-slate-300 font-mono whitespace-nowrap">
                ${formatNumber(stats.steps)} 步
              </td>
              <td class="py-2.5 px-3 text-right text-xs text-amber-500 font-mono whitespace-nowrap">
                ${formatShort(stats.cache)}
              </td>
              <td class="py-2.5 px-3 text-right text-xs text-slate-500 whitespace-nowrap">
                ${c.last_modified_time.slice(0, 10) || '-'}
              </td>
              <td class="py-2.5 px-3 text-center whitespace-nowrap">
                <button onclick="event.stopPropagation(); openDrawerByCid('${c.id}')" class="px-2 py-0.5 rounded text-[11px] bg-slate-200 dark:bg-slate-800 hover:bg-blue-600 hover:text-white dark:hover:bg-cyan-500 text-slate-700 dark:text-slate-300 border theme-border font-medium whitespace-nowrap">
                  查看
                </button>
              </td>
            </tr>
          `;
        });

        const safePath = (proj.path || '').split(String.fromCharCode(92)).join('/');

        card.innerHTML = `
          <!-- 项目卡片 Header -->
          <div class="p-4 cursor-pointer flex flex-col md:flex-row md:items-center justify-between gap-3 bg-gradient-to-r from-slate-50 via-white/80 to-blue-50/30 dark:from-slate-900/90 dark:via-slate-800/80 dark:to-blue-950/20 hover:bg-slate-200/50 dark:hover:bg-slate-800/90 transition-all select-none" onclick="toggleProjectItem(${pIdx})">
            <div class="flex items-start md:items-center gap-3 flex-1 min-w-0">
              <span class="mt-0.5 md:mt-0 p-1.5 rounded-xl bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}" id="proj-arrow-${pIdx}">
                <svg class="w-4 h-4 transform" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
              </span>
              <div class="min-w-0 flex-1">
                <div class="flex items-center gap-2 flex-wrap whitespace-nowrap">
                  <span class="text-base font-black text-slate-900 dark:text-white truncate">📁 ${proj.name}</span>
                  <span class="px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-blue-500/15 text-blue-600 dark:text-cyan-400 border border-blue-500/20 whitespace-nowrap">${proj.conv_count} 个会话</span>
                  ${proj.billed_tokens > 10000000 ? '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-red-500/15 text-red-500 border border-red-500/20 whitespace-nowrap">核心项目</span>' : ''}
                </div>
                <div class="text-[11px] text-slate-400 font-mono mt-0.5 flex items-center gap-2 truncate whitespace-nowrap" title="${proj.path}">
                  <span class="truncate">路径: ${proj.path}</span>
                  ${proj.path !== '无本地工作区绑定' ? `<button onclick="event.stopPropagation(); copyToClipboard('${safePath}', '项目路径已复制')" class="text-blue-500 hover:underline">复制路径</button>` : ''}
                </div>
              </div>
            </div>

            <!-- 项目聚合指标胶囊 (StudyHelp 风格马卡龙多色胶囊) -->
            <div class="flex items-center gap-2 self-end md:self-auto flex-wrap whitespace-nowrap">
              <div class="text-center px-3 py-1.5 rounded-xl bg-purple-50/90 dark:bg-purple-950/40 border border-purple-200/80 dark:border-purple-800/50 whitespace-nowrap shadow-sm">
                <div class="text-[10px] font-bold text-purple-700 dark:text-purple-300">项目对话轮数</div>
                <div class="text-sm md:text-base font-black text-purple-900 dark:text-purple-100 font-mono">${formatNumber(proj.user_turns)} 轮</div>
              </div>
              <div class="text-right px-3 py-1.5 rounded-xl bg-cyan-50/90 dark:bg-cyan-950/40 border border-cyan-200/80 dark:border-cyan-800/50 whitespace-nowrap shadow-sm">
                <div class="text-[10px] font-bold text-cyan-700 dark:text-cyan-300">项目新增 Token</div>
                <div class="text-sm md:text-base font-black text-cyan-900 dark:text-cyan-100 font-mono">${formatShort(proj.billed_tokens)}</div>
              </div>
              <div class="text-center px-2.5 py-1.5 rounded-xl bg-slate-100/90 dark:bg-slate-800/70 border theme-border whitespace-nowrap shadow-sm">
                <div class="text-[10px] font-bold text-slate-500 dark:text-slate-400">执行步骤</div>
                <div class="text-xs md:text-sm font-black text-slate-800 dark:text-slate-200 font-mono">${formatNumber(proj.step_count)} 步</div>
              </div>
              <div class="text-right px-2 py-1 text-xs text-slate-500 dark:text-slate-400 hidden lg:block whitespace-nowrap">
                <div>缓存率: <span class="font-bold text-amber-500 font-mono">${proj.cache_hit_rate}%</span></div>
                <div class="text-[10px] text-slate-400 mt-0.5">活跃: ${proj.last_modified_time ? proj.last_modified_time.slice(0, 10) : '-'}</div>
              </div>
            </div>
          </div>

          <!-- 项目折叠内容：项目下所有具体对话表格 -->
          <div class="border-t theme-border overflow-x-auto ${isOpen ? '' : 'hidden'}" id="proj-body-${pIdx}">
            <table class="w-full text-left text-xs whitespace-nowrap">
              <thead class="theme-subtle text-slate-500 dark:text-slate-400 border-b theme-border font-medium whitespace-nowrap">
                <tr>
                  <th class="py-2.5 px-3 whitespace-nowrap">所属具体会话标题与 ID</th>
                  <th class="py-2.5 px-3 text-right whitespace-nowrap">新增 Token</th>
                  <th class="py-2.5 px-3 text-center whitespace-nowrap">新增对话轮数</th>
                  <th class="py-2.5 px-3 text-center whitespace-nowrap">执行步骤数</th>
                  <th class="py-2.5 px-3 text-right whitespace-nowrap">Prompt 缓存命中</th>
                  <th class="py-2.5 px-3 text-right whitespace-nowrap">更新时间</th>
                  <th class="py-2.5 px-3 text-center w-16 whitespace-nowrap">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y theme-border text-slate-700 dark:text-slate-300">
                ${convRowsHtml}
              </tbody>
            </table>
          </div>
        `;

        container.appendChild(card);
      });
      syncToggleAllButtonState();
    }

    function updateToggleAllButton(isAllOpen) {
      const btn = document.getElementById('btnToggleAllProjects');
      if (!btn) return;
      if (isAllOpen) {
        btn.innerHTML = `
          <svg class="w-3.5 h-3.5 text-cyan-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 11l7-7 7 7M5 19l7-7 7 7"/></svg>
          <span>全部折叠</span>
        `;
        btn.title = "点击折叠所有项目";
      } else {
        btn.innerHTML = `
          <svg class="w-3.5 h-3.5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 13l-7 7-7-7m14-8l-7 7-7-7"/></svg>
          <span>全部展开</span>
        `;
        btn.title = "点击展开所有项目";
      }
    }

    function syncToggleAllButtonState() {
      const totalCards = document.querySelectorAll('[id^="proj-card-"]').length;
      const openBodies = document.querySelectorAll('[id^="proj-body-"]:not(.hidden)').length;
      const isAllOpen = totalCards > 0 && (openBodies === totalCards);
      updateToggleAllButton(isAllOpen);
    }

    function toggleAllProjectsCombined() {
      const totalCards = document.querySelectorAll('[id^="proj-card-"]').length;
      const openBodies = document.querySelectorAll('[id^="proj-body-"]:not(.hidden)').length;
      const targetOpen = (openBodies < totalCards);
      toggleAllProjects(targetOpen);
      updateToggleAllButton(targetOpen);
    }

    function toggleProjectItem(idx) {
      const body = document.getElementById(`proj-body-${idx}`);
      const arrow = document.getElementById(`proj-arrow-${idx}`);
      const card = document.getElementById(`proj-card-${idx}`);
      if (!body) return;

      const projName = card ? card.dataset.projName : null;
      const isCurrentlyHidden = body.classList.contains('hidden');
      if (isCurrentlyHidden) {
        body.classList.remove('hidden');
        if (arrow) arrow.classList.add('rotate-180');
        if (projName) projectAccordionOpenState[projName] = true;
      } else {
        body.classList.add('hidden');
        if (arrow) arrow.classList.remove('rotate-180');
        if (projName) projectAccordionOpenState[projName] = false;
      }
      syncToggleAllButtonState();
    }

    function toggleAllProjects(openAll) {
      const cards = document.querySelectorAll('[id^="proj-card-"]');
      cards.forEach(card => {
        const pIdx = card.id.replace('proj-card-', '');
        const body = document.getElementById(`proj-body-${pIdx}`);
        const arrow = document.getElementById(`proj-arrow-${pIdx}`);
        const projName = card.dataset.projName;
        if (projName) projectAccordionOpenState[projName] = openAll;
        if (body) {
          if (openAll) {
            body.classList.remove('hidden');
            if (arrow) arrow.classList.add('rotate-180');
          } else {
            body.classList.add('hidden');
            if (arrow) arrow.classList.remove('rotate-180');
          }
        }
      });
      updateToggleAllButton(openAll);
    }

    function openDrawerByCid(cid) {
      const target = (window.ALL_CONVERSATIONS || []).find(c => c.id === cid);
      if (target) {
        openDrawer(target);
      }
    }

    // ==========================================
    // 抽屉展开逻辑与逐轮记录展示
    // ==========================================
    function openDrawer(item) {
      currentDetailItem = item;
      document.getElementById('drawerTitle').innerText = item.title;
      document.getElementById('drawerCid').innerText = item.id;
      
      document.getElementById('drawerProjectName').innerText = item.project_name || '全局 / 独立会话';
      document.getElementById('drawerProjectPath').innerText = item.project_path || '无本地工作区绑定';

      const sourceTag = document.getElementById('drawerSourceTag');
      if (item.is_official) {
        sourceTag.innerHTML = '<span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">官方原装精确数据</span>';
      } else {
        sourceTag.innerHTML = '<span class="px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20">早期历史日志 (思维链字符级 NLP 估算)</span>';
      }

      // 获取当前上下文增量数据
      const stats = getConversationStatsForContext(item);
      const hasTimeScope = Boolean(currentSpecificDate || currentTimeCategory === 'today');
      const prefix = item.is_estimated ? '~' : '';

      const ctxBadge = document.getElementById('drawerContextBadge');
      if (currentSpecificDate) {
        ctxBadge.innerText = `📅 ${currentSpecificDate} 新增`;
        ctxBadge.classList.remove('hidden');
      } else if (currentTimeCategory === 'today') {
        ctxBadge.innerText = '📅 今日新增';
        ctxBadge.classList.remove('hidden');
      } else {
        ctxBadge.classList.add('hidden');
      }

      // 4 宫格卡片填充
      if (hasTimeScope) {
        document.getElementById('drawerTurnsTitle').innerText = '💬 当日新增轮数';
        document.getElementById('drawerTurns').innerText = stats.turns + ' 轮';
        document.getElementById('drawerTurnsSub').innerText = `历史累计: ${stats.total_turns} 轮`;

        document.getElementById('drawerStepsTitle').innerText = '⚡ 当日执行步骤';
        document.getElementById('drawerSteps').innerText = formatNumber(stats.steps) + ' 步';
        document.getElementById('drawerStepsSub').innerText = `历史累计: ${formatNumber(stats.total_steps)} 步`;

        document.getElementById('drawerBilledTitle').innerText = '🔥 当日净计费';
        document.getElementById('drawerNewTokens').innerText = `${prefix}${formatShort(stats.billed)}`;
        document.getElementById('drawerBilledSub').innerText = `历史累计: ${formatShort(stats.total_billed)}`;
      } else {
        document.getElementById('drawerTurnsTitle').innerText = '💬 对话总轮数';
        document.getElementById('drawerTurns').innerText = stats.total_turns + ' 轮';
        document.getElementById('drawerTurnsSub').innerText = '提问交互';

        document.getElementById('drawerStepsTitle').innerText = '⚡ 执行总步骤';
        document.getElementById('drawerSteps').innerText = formatNumber(stats.total_steps) + ' 步';
        document.getElementById('drawerStepsSub').innerText = '自主动作';

        document.getElementById('drawerBilledTitle').innerText = '🔥 累计净计费';
        document.getElementById('drawerNewTokens').innerText = `${prefix}${formatShort(stats.total_billed)}`;
        document.getElementById('drawerBilledSub').innerText = `${formatNumber(stats.total_billed)} tokens`;
      }

      document.getElementById('drawerRate').innerText = stats.cache_rate + ' %';
      document.getElementById('drawerCacheTokens').innerText = `已节省: ${formatShort(stats.cache)}`;

      // Token 详细构成 (大字号突出呈现 + 精确数字副标)
      document.getElementById('drawerInput').innerText = `${prefix}${formatShort(stats.input)}`;
      const inExact = document.getElementById('drawerInputExact');
      if (inExact) inExact.innerText = `(${formatNumber(stats.input)})`;

      document.getElementById('drawerCache').innerText = `${prefix}${formatShort(stats.cache)}`;
      const cExact = document.getElementById('drawerCacheExact');
      if (cExact) cExact.innerText = `(${formatNumber(stats.cache)})`;

      document.getElementById('drawerOutput').innerText = `${prefix}${formatShort(stats.output)}`;
      const outExact = document.getElementById('drawerOutputExact');
      if (outExact) outExact.innerText = `(${formatNumber(stats.output)})`;

      // 活跃日期分布标签
      const daysContainer = document.getElementById('drawerDaysBreakdownList');
      daysContainer.innerHTML = '';
      const breakdown = item.daily_breakdown || {};
      const daysKeys = Object.keys(breakdown).sort().reverse();
      document.getElementById('drawerActiveDaysTag').innerText = `跨越 ${daysKeys.length} 个活跃日`;

      daysKeys.forEach(dDay => {
        const dData = breakdown[dDay];
        const isCur = (currentSpecificDate === dDay);
        const pill = document.createElement('span');
        pill.className = `px-2 py-0.5 rounded-lg border font-mono text-[10px] cursor-pointer transition-all ${
          isCur ? 'bg-blue-600 text-white border-blue-500 shadow-sm' : 'theme-subtle hover:border-cyan-500 text-slate-600 dark:text-slate-300'
        }`;
        pill.title = `点击切换至 ${dDay} 视角\n该日提问: ${dData.user_turns} 轮\n净计费: ${formatShort(dData.billed_tokens)}`;
        pill.innerHTML = `📅 ${dDay.slice(5)}: +${dData.user_turns}轮 · ${formatShort(dData.billed_tokens)}`;
        pill.onclick = () => {
          filterBySpecificDate(dDay);
          openDrawer(item);
        };
        daysContainer.appendChild(pill);
      });

      // 初始化逐轮对话过滤控制器与对话列表
      drawerShowOnlyScoped = Boolean(currentSpecificDate || currentTimeCategory === 'today');
      updateTurnOrderButtonUI();
      renderDrawerTurns();

      // Footer 时间
      document.getElementById('drawerFooterTime').innerText = `更新时间: ${item.last_modified_time || '-'}`;

      const drawer = document.getElementById('drawer');
      const overlay = document.getElementById('drawerOverlay');

      // 确保应用合规的宽度 (读取 localStorage 或默认标准舒适宽度 840px)
      const savedW = localStorage.getItem('antigravity_drawer_width');
      if (savedW) {
        const w = parseInt(savedW);
        if (w >= 480 && w <= window.innerWidth - 40) {
          drawer.style.width = w + 'px';
        } else {
          drawer.style.width = Math.min(840, window.innerWidth - 40) + 'px';
        }
      } else {
        drawer.style.width = Math.min(840, window.innerWidth - 40) + 'px';
      }

      drawer.classList.remove('hidden');
      overlay.classList.remove('hidden');
      setTimeout(() => {
        drawer.classList.remove('translate-x-full');
      }, 10);
    }

    let drawerTurnOrder = 'desc'; // 默认：最新的一轮对话放在最上面 (倒序置顶)

    function toggleDrawerTurnOrder() {
      drawerTurnOrder = (drawerTurnOrder === 'desc') ? 'asc' : 'desc';
      updateTurnOrderButtonUI();
      renderDrawerTurns();
    }

    function updateTurnOrderButtonUI() {
      const text = document.getElementById('turnOrderText');
      const btn = document.getElementById('btnToggleTurnOrder');
      if (!text) return;
      if (drawerTurnOrder === 'desc') {
        text.innerText = '最新在最上';
        if (btn) btn.title = '当前：最新对话在最上面（倒序）。点击切换为最早对话在前（正序）';
      } else {
        text.innerText = '最早在最上';
        if (btn) btn.title = '当前：最早对话在最上面（正序）。点击切换为最新对话在前（倒序）';
      }
    }

    function setDrawerTurnsFilter(onlyScoped) {
      drawerShowOnlyScoped = onlyScoped;
      renderDrawerTurns();
    }

    function renderDrawerTurns() {
      if (!currentDetailItem) return;
      const item = currentDetailItem;
      const allTurns = item.turns_list || [];
      const turnsListContainer = document.getElementById('drawerTurnsList');
      const filterContainer = document.getElementById('drawerTurnFilterContainer');
      const btnScoped = document.getElementById('btnDrawerFilterScoped');
      const btnAll = document.getElementById('btnDrawerFilterAll');
      const badge = document.getElementById('drawerTurnBadge');

      const targetDay = currentSpecificDate || (currentTimeCategory === 'today' ? window.DASHBOARD_DATA.today_str : null);

      if (targetDay && allTurns.some(t => t.day === targetDay)) {
        filterContainer.classList.remove('hidden');
        if (drawerShowOnlyScoped) {
          btnScoped.className = 'px-2.5 py-0.5 rounded font-bold bg-blue-600 text-white shadow-sm transition-all';
          btnAll.className = 'px-2.5 py-0.5 rounded text-slate-500 hover:text-slate-900 dark:hover:text-white transition-all';
        } else {
          btnScoped.className = 'px-2.5 py-0.5 rounded text-slate-500 hover:text-slate-900 dark:hover:text-white transition-all';
          btnAll.className = 'px-2.5 py-0.5 rounded font-bold bg-blue-600 text-white shadow-sm transition-all';
        }
      } else {
        filterContainer.classList.add('hidden');
        drawerShowOnlyScoped = false;
      }

      let displayedTurns = [...allTurns];
      if (drawerShowOnlyScoped && targetDay) {
        displayedTurns = displayedTurns.filter(t => t.day === targetDay);
        badge.innerText = `当日新增 ${displayedTurns.length} 轮 (共 ${allTurns.length} 轮)`;
      } else {
        badge.innerText = `全量历史共 ${allTurns.length} 轮`;
      }

      // 根据排序方向决定最新或最早在前（默认 desc：最新的一轮对话排在最上面）
      if (drawerTurnOrder === 'desc') {
        displayedTurns.sort((a, b) => b.turn - a.turn);
      } else {
        displayedTurns.sort((a, b) => a.turn - b.turn);
      }

      turnsListContainer.innerHTML = '';
      if (displayedTurns.length === 0) {
        turnsListContainer.innerHTML = `<div class="text-xs text-slate-400 py-12 text-center">当前时间范围内暂无新增对话提问</div>`;
      } else {
        displayedTurns.forEach((t, idx) => {
          const isLatest = (drawerTurnOrder === 'desc' && idx === 0) || (drawerTurnOrder === 'asc' && idx === displayedTurns.length - 1);
          const div = document.createElement('div');
          div.className = `w-full p-3.5 md:p-4 rounded-xl border theme-border transition-all shadow-sm ${
            isLatest 
              ? 'bg-blue-50/70 dark:bg-slate-800/95 border-blue-200 dark:border-blue-700/60 ring-1 ring-blue-500/20' 
              : 'bg-slate-100/90 dark:bg-slate-800/80 hover:border-cyan-500/40'
          }`;
          div.innerHTML = `
            <div class="flex items-center justify-between text-[11px] md:text-xs text-slate-500 dark:text-slate-400 font-mono mb-2 pb-1.5 border-b theme-border">
              <span class="font-extrabold text-blue-600 dark:text-cyan-400 flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full ${isLatest ? 'bg-emerald-500 shadow-sm shadow-emerald-500/50' : 'bg-cyan-400 shadow-sm shadow-cyan-400/50'}"></span>
                第 ${t.turn} 轮对话
                ${isLatest ? '<span class="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 font-bold border border-emerald-500/20 ml-1">最新</span>' : ''}
              </span>
              <span class="text-slate-400 font-mono">🕒 ${t.time || t.day || ''}</span>
            </div>
            <div class="text-slate-900 dark:text-slate-100 font-sans whitespace-pre-wrap break-words leading-relaxed text-xs md:text-sm font-medium">${t.prompt}</div>
          `;
          turnsListContainer.appendChild(div);
        });
      }
    }

    function closeDrawer() {
      const drawer = document.getElementById('drawer');
      const overlay = document.getElementById('drawerOverlay');
      drawer.classList.add('translate-x-full');
      setTimeout(() => {
        drawer.classList.add('hidden');
        overlay.classList.add('hidden');
      }, 300);
    }

    function exportToCSV() {
      const headers = ['ID', 'Title', 'ProjectName', 'ProjectPath', 'TimeCategory', 'ScopedTurns', 'TotalTurns', 'ScopedSteps', 'TotalSteps', 'ScopedBilledTokens', 'TotalBilledTokens', 'LastModified', 'IsEstimated'];
      const rows = filteredData.map(d => {
        const stats = d._ctx || getConversationStatsForContext(d);
        return [
          `"${d.id}"`,
          `"${d.title.replace(/"/g, '""')}"`,
          `"${(d.project_name || '').replace(/"/g, '""')}"`,
          `"${(d.project_path || '').replace(/"/g, '""')}"`,
          `"${d.time_category}"`,
          stats.turns,
          stats.total_turns,
          stats.steps,
          stats.total_steps,
          stats.billed,
          stats.total_billed,
          `"${d.last_modified_time}"`,
          d.is_estimated ? 'Yes' : 'No'
        ];
      });
      const csvContent = '\uFEFF' + [headers.join(','), ...rows.map(r => r.join(','))].join(String.fromCharCode(10));
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `antigravity_token_usage_${new Date().toISOString().slice(0, 10)}.csv`;
      link.click();
      showToast('CSV 导出完成');
    }

    document.getElementById('searchInput').addEventListener('input', applyFilterAndSort);
    document.getElementById('filterLevel').addEventListener('change', applyFilterAndSort);
    document.getElementById('filterSource').addEventListener('change', applyFilterAndSort);
    document.getElementById('pageSizeSelect').addEventListener('change', (e) => {
      pageSize = e.target.value;
      currentPage = 1;
      renderTable();
    });

    document.getElementById('projectSearchInput').addEventListener('input', renderProjectsView);

    window.addEventListener('resize', () => {
      if (lockedTrendDate && typeof repositionPinnedTooltip === 'function') {
        const s = window.DASHBOARD_DATA;
        const idx = s?.trend?.labels?.indexOf(lockedTrendDate);
        if (idx !== undefined && idx >= 0) {
          repositionPinnedTooltip(idx);
        }
      }
    });

    window.addEventListener('DOMContentLoaded', () => {
      initTheme();
      initKPIs();
      initCharts();
      initDrawerResize();
      initSnapshots();
      applyFilterAndSort();
      const savedView = localStorage.getItem('antigravity_active_view') || 'project';
      switchView(savedView);

      // 支持 URL 参数覆盖 (便于演示与无头自动化截图)
      const urlParams = new URLSearchParams(window.location.search);
      if (urlParams.get('theme') === 'dark') document.documentElement.classList.add('dark');
      if (urlParams.get('theme') === 'light') document.documentElement.classList.remove('dark');
      if (urlParams.get('view')) switchView(urlParams.get('view'));
      if (urlParams.get('expand') === 'all') {
        setTimeout(() => { if (typeof toggleAllProjectsCombined === 'function') toggleAllProjectsCombined(); }, 200);
      }
    });
  </script>
</body>
</html>
"""
    return html_template

def main():
    parser = argparse.ArgumentParser(description="Antigravity Token 仪表盘生成器 (V6.0 项目下钻版)")
    parser.add_argument("--dir", type=str, default=None, help="显式指定 Antigravity 数据目录")
    args = parser.parse_args()

    antigravity_dir = detect_antigravity_dir(args.dir)
    print("=" * 68)
    print(f"🔍 探测到 Antigravity 根目录: {antigravity_dir}")
    print("=" * 68)

    summary, conversations = load_data(antigravity_dir)
    official_cnt = sum(1 for c in conversations if c['is_official'])
    estimated_cnt = sum(1 for c in conversations if c['is_estimated'])
    print(f"📊 扫描全部会话: 共 {summary['total_conversations']} 个")
    print(f"📂 聚合识别项目: 共 {summary['total_projects']} 个独立工程工作区")
    print(f"   ├─ 今日/近24小时会话 : {summary['today']['count']} 个 (计费: {summary['today']['billed']:,} tokens)")
    print(f"   ├─ 全局总对话轮数    : {summary['total_user_turns']:,} 轮 (用户真实提问交互)")
    print(f"   ├─ Agent幕后总步骤数 : {summary['total_steps']:,} 步 (思考+工具调用)")
    print(f"   ├─ 官方原装精确会话  : {official_cnt} 个")
    print(f"   └─ 历史智能估算会话  : {estimated_cnt} 个")
    print(f"💰 累计净计费 Token   : {summary['total_billed_tokens']:,} tokens")

    json_path = os.path.join(CURRENT_DIR, "data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "conversations": conversations}, f, ensure_ascii=False, indent=2)

    snap_file = save_snapshot(summary, conversations)

    html_content = generate_html(summary, conversations)
    html_path = os.path.join(CURRENT_DIR, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ V7.0 项目聚合透视与具体会话下钻看板已生成: {html_path}")
    print(f"📦 历史扫描快照已归档: {snap_file}")
    print("=" * 68)

if __name__ == "__main__":
    main()
