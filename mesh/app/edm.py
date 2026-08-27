"""GeekPark Mesh · EDM（预览邮件）生成与发送"""
import os, smtplib, html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

F = "-apple-system,'PingFang SC','Hiragino Sans GB','Microsoft YaHei','Helvetica Neue',Arial,sans-serif"
DEEP, INK, INK2, INK3, LINE, TIF, GREEN = "#14382F", "#0B0B0C", "#3F4650", "#6B7280", "#E6EDE0", "#0AA79F", "#8CC43A"
FLAG_COLORS = {"两处记录待核对": "#EA580C", "一方有需求，另一方尚未接触": "#0891B2", "一方接触了，另一方正在关注": "#16A34A", "已公开报道，内部也在用": "#2563EB",
               "同一件事，两个部门各知一半": "#9333EA", "采访对象也是客户": "#DB2777", "两个部门各有判断": "#64748B", "已联动": "#166534",
               "一方接触，另一方用得上": "#854D0E", "外部在热聊，我们还没碰": "#4338CA", "海外接触，国内可能承接": "#0D9488", "海外新发现，国内尚未接触": "#0369A1",
               "中英文站同周各自成稿": "#0E7490", "一方报道了，另一方在接触": "#7C3AED", "同一赛道，各自在做": "#57534E", "同一公司，不同触点": "#57534E", "已排期，内容侧待安排": "#9D174D"}

def _card(r):
    color = FLAG_COLORS.get(r.get("label", ""), "#344054")
    srcs = " · ".join(r.get("sources", []))
    return f'''
<tr><td style="padding:0 0 12px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {LINE};border-radius:12px;background-color:#FFFFFF;" bgcolor="#FFFFFF">
<tr><td style="padding:16px 18px 6px 18px;font-family:{F};"><span style="display:inline-block;font-size:12px;line-height:18px;font-weight:700;color:#FFFFFF;background-color:{color};border-radius:999px;padding:1px 10px;">{html.escape(r.get("label",""))}</span></td></tr>
<tr><td style="padding:0 18px 4px 18px;font-family:{F};font-size:18px;line-height:26px;font-weight:700;color:{INK};">{html.escape(r.get("title",""))}</td></tr>
<tr><td style="padding:0 18px 10px 18px;font-family:{F};font-size:14px;line-height:22px;color:{INK2};">{html.escape(r.get("body",""))}</td></tr>
<tr><td style="padding:0 18px 14px 18px;font-family:{F};font-size:12px;line-height:18px;color:{INK3};"><span style="color:{DEEP};font-weight:700;">来源</span>&nbsp; {srcs}</td></tr>
</table></td></tr>'''

def _btn(t, u, pri=True):
    bg = DEEP if pri else "#FFFFFF"; fg = "#FFFFFF" if pri else DEEP
    return f'''<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="display:inline-table;margin:0 8px 8px 0;"><tr><td align="center" bgcolor="{bg}" style="border-radius:999px;border:1.5px solid {DEEP};background-color:{bg};"><a href="{u}" style="display:inline-block;padding:11px 20px;font-family:{F};font-size:14px;line-height:18px;font-weight:700;color:{fg};text-decoration:none;">{t}</a></td></tr></table>'''

def render_edm(issue: dict, data: dict, base_url: str, logo_url: str = "") -> tuple[str, str]:
    """返回 (html, text)"""
    slug = issue["slug"]; url = f"{base_url.rstrip('/')}/{slug}"
    rels = data.get("relations", [])[:4]
    kpis = data.get("kpis", [])
    logo = (f'<img src="{logo_url}" width="132" height="90" alt="GEEKPARK Mesh" border="0" style="display:block;width:132px;height:90px;">' if logo_url else
            f'<div style="font-size:22px;line-height:24px;font-weight:800;letter-spacing:2px;color:{INK};">GEEKPARK</div><div style="font-size:44px;line-height:46px;font-weight:800;letter-spacing:-1px;color:{GREEN};margin-top:2px;">Mesh</div><div style="width:112px;height:5px;background-color:{GREEN};border-radius:3px;margin-top:2px;"></div>')
    kpi_html = "".join(f'<td style="padding:0 28px 0 0;font-family:{F};"><div style="font-size:28px;line-height:32px;font-weight:800;color:{DEEP};">{html.escape(k.get("n",""))}</div><div style="font-size:12px;line-height:18px;color:{INK3};">{html.escape(k.get("label",""))}</div></td>' for k in kpis)
    entries = [("2. 接触过的人和公司", "#who", "名字与公司实名，悬停可看身份、时间、要点与来源。"),
               ("3. 关注了什么", "#what", "关注的领域、公司与人、技术、类型，按关注度或按部门排序。"),
               ("4. 日程与计划", "#next", "各团队日程或会议里出现过的、还没发生的事，每项标确定度；不代表承诺，不构成任务。"),
               ("5. 沟通中提到的看法", "#views", "各团队例会和对话里顺口说到的判断，非结论、不追溯发言人。")]
    entry_rows = "".join(f'<tr><td style="padding:14px 18px;border-bottom:1px solid {LINE};font-family:{F};font-size:14px;line-height:22px;color:{INK2};"><span style="font-weight:700;color:{INK};">{t}</span> — {d} <a href="{url}{a}" style="color:{TIF};text-decoration:underline;">打开</a></td></tr>' for t, a, d in entries)
    n_rel = len(data.get("relations", []))
    h = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><meta http-equiv="X-UA-Compatible" content="IE=edge"><title>GeekPark Mesh · 周报 · {html.escape(issue["period_label"])}</title>
<!--[if mso]><style>table,td{{font-family:Arial,'Microsoft YaHei',sans-serif !important;}}</style><![endif]--></head>
<body style="margin:0;padding:0;background-color:#F5F7F4;">
<div style="display:none;font-size:1px;color:#F5F7F4;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">这两周极客公园接触了谁、关注了什么、和谁产生了关系 · {n_rel} 组可同步的关系 · 全文在 Mesh</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#F5F7F4" style="background-color:#F5F7F4;"><tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">
<tr><td style="padding:0 0 18px 0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
<td align="left" valign="bottom" style="font-family:{F};">{logo}</td>
<td align="right" valign="bottom" style="font-family:{F};font-size:13px;line-height:18px;color:{INK3};">周报 · {html.escape(issue.get("version","v1.4"))}<br><span style="font-size:22px;line-height:28px;font-weight:700;color:#CBD0D6;">{html.escape(issue["period_label"])}</span></td></tr></table></td></tr>
<tr><td style="padding:22px 24px;background-color:#FFFFFF;border:1px solid {LINE};border-radius:16px;font-family:{F};" bgcolor="#FFFFFF">
<div style="font-size:16px;line-height:26px;font-weight:700;color:{INK};">{html.escape(data.get("question",""))}</div>
<div style="font-size:15px;line-height:25px;color:{INK2};margin-top:8px;">{html.escape(data.get("lead",""))}</div>
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:18px;"><tr>{kpi_html}</tr></table></td></tr>
<tr><td style="padding:26px 0 10px 0;font-family:{F};"><div style="font-size:20px;line-height:28px;font-weight:800;color:{INK};">1. 可同步的关系 <span style="font-size:13px;font-weight:600;color:{INK3};">· 邮件只列前 {len(rels)} 组，共 {n_rel} 组</span></div>
<div style="font-size:13px;line-height:20px;color:{INK3};margin-top:2px;">被不止一个团队碰到，或一个团队碰到、另一个明显用得上。只列各团队各自的事实，不给建议。</div></td></tr>
{"".join(_card(r) for r in rels)}
<tr><td style="padding:2px 0 8px 0;font-family:{F};">{_btn(f"查看全部 {n_rel} 组关系", url + "#rel")}</td></tr>
<tr><td style="padding:22px 0 8px 0;font-family:{F};font-size:20px;line-height:28px;font-weight:800;color:{INK};">其余四节，在 Mesh 里看</td></tr>
<tr><td style="padding:0;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {LINE};border-radius:12px;background-color:#FFFFFF;" bgcolor="#FFFFFF">{entry_rows}</table></td></tr>
<tr><td style="padding:22px 0 0 0;font-family:{F};"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {LINE};border-radius:16px;background-color:#FFFFFF;" bgcolor="#FFFFFF"><tr><td style="padding:18px 22px;font-family:{F};">
<div style="font-size:15px;line-height:24px;font-weight:700;color:{INK};">在 Mesh 里搜人、搜公司、搜关键词，或直接问一句话</div>
<div style="margin-top:12px;">{_btn("打开 Mesh 搜索", url + "?q=")}{_btn("往期周报", base_url.rstrip('/') + "/archive", False)}</div></td></tr></table></td></tr>
<tr><td style="padding:24px 8px 0 8px;font-family:{F};font-size:12px;line-height:20px;color:{INK3};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-radius:12px;background-color:{DEEP};" bgcolor="{DEEP}"><tr><td style="padding:12px 16px;font-family:{F};font-size:13px;line-height:20px;color:#FFFFFF;"><span style="font-weight:700;letter-spacing:1px;">仅限极客公园内部使用</span>&nbsp; <span style="color:#C7D6CF;">请勿转发、截图或对外引用；邮件内人名与公司为内部沟通记录，非公开信息。</span></td></tr></table>
<div style="margin-top:12px;">此邮件由 GeekPark Mesh 在管理员确认后发出 · 数据最近更新：{html.escape(issue.get("updated_at",""))} · 内部 · {html.escape(issue.get("version","v1.4"))}</div>
<div style="margin-top:4px;">脱敏在分发层执行 · <a href="{url}" style="color:{TIF};text-decoration:underline;">在浏览器中查看本期</a></div>
<div style="margin-top:14px;text-align:center;color:#9CA3AF;letter-spacing:2px;">© GEEKPARK</div></td></tr>
</table></td></tr></table></body></html>'''
    t = [f"GeekPark Mesh · 周报 · {issue['period_label']}（内部 · {issue.get('version','v1.4')}）", "", data.get("question", ""), data.get("lead", ""), "",
         " ｜ ".join(f"{k.get('n')} {k.get('label')}" for k in kpis), "", f"1. 可同步的关系（邮件只列前 {len(rels)} 组，共 {n_rel} 组）"]
    for r in rels: t.append(f"· {r.get('title')} —— {r.get('label')}。来源：{' · '.join(r.get('sources', []))}")
    t += [f"查看全部：{url}#rel", ""] + [f"{n} {url}{a}" for n, a, _ in entries] + ["", f"搜索 / 提问：{url}?q=   往期：{base_url.rstrip('/')}/archive", "",
          "仅限极客公园内部使用。请勿转发、截图或对外引用。", f"此邮件由 GeekPark Mesh 在管理员确认后发出 · 数据最近更新：{issue.get('updated_at','')}", "© GEEKPARK"]
    return h, "\n".join(t)

def send_mail(to_addrs: list[str], subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    host = os.environ.get("SMTP_HOST"); port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER"); pw = os.environ.get("SMTP_PASSWORD"); sender = os.environ.get("SMTP_FROM", user)
    if not (host and user and pw): return False, "未配置 SMTP（SMTP_HOST/SMTP_USER/SMTP_PASSWORD）"
    msg = MIMEMultipart("alternative"); msg["Subject"] = subject; msg["From"] = sender; msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(text_body, "plain", "utf-8")); msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        if os.environ.get("SMTP_SSL", "1") == "1":
            s = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            s = smtplib.SMTP(host, port, timeout=30); s.starttls()
        s.login(user, pw); s.sendmail(sender, to_addrs, msg.as_string()); s.quit()
        return True, ""
    except Exception as e:
        return False, str(e)
