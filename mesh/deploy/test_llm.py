import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
base = os.environ["MESH_LLM_BASE_URL"].rstrip("/")
key = os.environ["MESH_LLM_API_KEY"]
model = os.environ["MESH_LLM_MODEL"]

def call(label, messages, max_tokens=100):
    r = requests.post(
        base + "/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "max_tokens": max_tokens, "messages": messages},
        timeout=300,
    )
    print(f"[{label}] status={r.status_code}")
    print(r.text[:1000])
    print()

call("short", [{"role": "user", "content": "reply ok"}], 20)

big = "测试文本。" * 25000
call(
    "large-extract",
    [
        {"role": "system", "content": '返回 JSON {"items":[]}'},
        {
            "role": "user",
            "content": f"数据类型：T1\n团队：编辑部\n文档标题：大文件\n\n【原文开始】\n{big[:170000]}\n【原文结束】\n\n请按输出格式返回 JSON。",
        },
    ],
    8000,
)
