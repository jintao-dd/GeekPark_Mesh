"""GeekPark Mesh · EDM（预览邮件）生成与发送

浏览器预览：issue_edm.html + style.css（与读者页一致）
正式邮件：edm_email_inline.html 全 table + 内联样式（飞书/Outlook 可解析）
"""
from __future__ import annotations

import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

APP_DIR = Path(__file__).resolve().parent
_env = Environment(
    loader=FileSystemLoader(str(APP_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)

FLAG_COLORS = {
    "两处记录待核对": "#EA580C",
    "一方有需求，另一方尚未接触": "#0891B2",
    "一方接触了，另一方正在接触": "#16A34A",
    "已公开报道，内部也在用": "#2563EB",
    "同一件事，两个部门各知一半": "#9333EA",
    "采访对象也是客户": "#DB2777",
    "两个部门各有判断": "#64748B",
    "已联动": "#166534",
    "一方接触，另一方用得上": "#854D0E",
    "外部在热聊，我们还没碰": "#4338CA",
    "海外接触，国内可能承接": "#0D9488",
    "海外新发现，国内尚未接触": "#0369A1",
    "中英文站同周各自成稿": "#0E7490",
    "一方报道了，另一方在接触": "#7C3AED",
    "同一赛道，各自在做": "#57534E",
    "同一公司，不同触点": "#57534E",
    "已排期，内容侧待安排": "#9D174D",
}

_EDM_SECTIONS = [
    ("接触过的人和公司", "#who", "这段时间接触的人和公司。人名实名、公司实名；融资条款、金额、谈判立场不在此处。鼠标悬停任一名字，查看身份、时间、要点与来源。"),
    ("关注了什么", "#what", "关注的领域、公司与人、技术、类型。鼠标悬停任一关键词，查看各团队分别以什么形式关注了它。"),
    ("日程与计划", "#next", "各团队日程或会议里出现过的、还没发生的事。确定度从「已排期」到「会上初步一提」不等，标在每一项后面；不代表承诺，也不构成任何团队或个人的任务。悬停查看各团队的状态与来源。"),
    ("沟通中提到的看法", "#views", "以下来自各团队例会和对话记录里顺口说到的判断，只是当时的一种看法，不代表公司或团队结论，也不对应到任何具体的人。放在这里是因为它们可能对别的团队有启发。"),
]


def _edm_ctx(issue: dict, data: dict, base_url: str) -> dict:
    data = data or {}
    data.setdefault("kpis", [])
    data.setdefault("relations", [])
    return {
        "issue": issue,
        "d": data,
        "n_rel": len(data.get("relations", [])),
        "base_url": base_url.rstrip("/"),
        "flag_colors": FLAG_COLORS,
    }


def _plain_text(issue: dict, data: dict, base_url: str) -> str:
    slug = issue["slug"]
    url = f"{base_url.rstrip('/')}/{slug}"
    rels = (data or {}).get("relations", [])[:4]
    kpis = (data or {}).get("kpis", [])
    n_rel = len((data or {}).get("relations", []))
    period = issue.get("period_label", "")
    version = issue.get("version", "v1.4")
    t = [
        f"GeekPark Mesh · 周报 · {period}（内部 · {version}）",
        "",
        (data or {}).get("question", ""),
        (data or {}).get("lead", ""),
        "",
        " ｜ ".join(f"{k.get('n')} {k.get('label')}" for k in kpis),
        "",
        f"可同步的关系（邮件只列前 {len(rels)} 组，共 {n_rel} 组）",
    ]
    for r in rels:
        t.append(f"· {r.get('title')} —— {r.get('label')}。来源：{' · '.join(r.get('sources', []))}")
    t += [f"查看全部：{url}#rel", ""]
    for title, anchor, desc in _EDM_SECTIONS:
        t.append(f"{title} — {desc} {url}{anchor}")
    t += [
        "",
        f"搜索 / 提问：{url}?q=   往期：{base_url.rstrip('/')}/archive",
        "",
        "仅限极客公园内部使用。请勿转发、截图或对外引用。",
        f"此邮件由 GeekPark Mesh 在管理员确认后发出 · 数据最近更新：{issue.get('updated_at', '')}",
        "© GEEKPARK",
    ]
    return "\n".join(t)


def render_edm(issue: dict, data: dict, base_url: str, logo_url: str = "") -> tuple[str, str]:
    """返回 (html, text)。邮件 HTML 为全内联样式，与邮箱客户端实际渲染一致。"""
    _ = logo_url
    ctx = _edm_ctx(issue, data, base_url)
    html_out = _env.get_template("edm_email_inline.html").render(**ctx)
    return html_out, _plain_text(issue, data, base_url)


def send_mail(to_addrs: list[str], subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM", user)
    if not (host and user and pw):
        return False, "未配置 SMTP（SMTP_HOST/SMTP_USER/SMTP_PASSWORD）"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        if os.environ.get("SMTP_SSL", "1") == "1":
            s = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            s = smtplib.SMTP(host, port, timeout=30)
            s.starttls()
        s.login(user, pw)
        s.sendmail(sender, to_addrs, msg.as_string())
        s.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def parse_addrs(raw: str) -> list[str]:
    return [a.strip() for a in re.split(r"[,\s;]+", (raw or "")) if a.strip()]


def default_to(con) -> str:
    from . import db

    return db.get_setting(con, "edm_default_to") or os.environ.get("EDM_DEFAULT_TO", "") or ""


def issue_auto_send_enabled(con, issue_id: int) -> bool:
    from . import db

    v = db.get_setting(con, f"edm_auto_send:{issue_id}")
    if v is not None:
        return v == "1"
    return (db.get_setting(con, "edm_auto_send_default") or "1") == "1"


def mail_status_label(con, issue_id: int) -> dict:
    rows = [
        dict(x)
        for x in con.execute(
            "SELECT subject, ok, error, at, to_addr FROM mail_log WHERE issue_id=? ORDER BY id DESC LIMIT 8",
            (issue_id,),
        )
    ]
    if not rows:
        return {"label": "未发", "kind": "none", "recent": rows}
    formal = [r for r in rows if not (r.get("subject") or "").startswith("【测试】")]
    if any(r.get("ok") for r in formal):
        last = next(r for r in formal if r.get("ok"))
        return {"label": f"已正式发 · {last.get('at', '')}", "kind": "formal", "recent": rows}
    if rows[0].get("ok"):
        subj = rows[0].get("subject") or ""
        if subj.startswith("【测试】"):
            return {"label": f"已测试 · {rows[0].get('at', '')}", "kind": "test", "recent": rows}
    return {"label": f"发送失败 · {rows[0].get('at', '')}", "kind": "fail", "recent": rows}


def send_for_issue(
    con,
    issue: dict,
    data: dict,
    *,
    to_addrs: list[str],
    test: bool,
    base_url: str,
    logo_url: str = "",
) -> tuple[bool, str, str]:
    """渲染并发送；写入 mail_log 并 commit。返回 (ok, error, subject)。"""
    if not test and issue.get("status") != "published":
        return False, "正式发送仅限已上线期", ""
    if not to_addrs:
        return False, "未填写收件人", ""
    h, t = render_edm(issue, data or {}, base_url, logo_url)
    subject = ("【测试】" if test else "") + (
        f"GeekPark Mesh · 周报 · {issue['period_label']}（内部 · {issue.get('version', 'v1')}）"
    )
    ok, err = send_mail(to_addrs, subject, h, t)
    con.execute(
        "INSERT INTO mail_log(issue_id,to_addr,subject,ok,error) VALUES(?,?,?,?,?)",
        (issue["id"], ",".join(to_addrs), subject, 1 if ok else 0, err),
    )
    con.commit()
    return ok, err or "", subject
