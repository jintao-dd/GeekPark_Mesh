import os, json, time, urllib.request

t0 = time.monotonic()
print('request_start_ms=0', flush=True)

api_key = os.environ['MESH_LLM_API_KEY']
base_url = os.environ.get('MESH_LLM_BASE_URL', 'https://kspmas.ksyun.com/v1')
model = os.environ.get('MESH_LLM_MODEL', 'mco-6')

payload = json.dumps({
    'model': model,
    'max_tokens': 4000,
    'stream': True,
    'messages': [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {'role': 'user', 'content': '你好啊'}
    ]
}).encode('utf-8')

req = urllib.request.Request(
    base_url.rstrip('/') + '/chat/completions',
    data=payload,
    headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'},
    method='POST'
)
resp = urllib.request.urlopen(req, timeout=300)
first_byte_ms = int((time.monotonic() - t0) * 1000)
print(f'first_byte_ms={first_byte_ms}', flush=True)

chunk_idx = 0
for raw in resp:
    line = raw.decode('utf-8').strip()
    if not line or not line.startswith('data:'):
        continue
    data = line[5:].strip()
    rel = int((time.monotonic() - t0) * 1000)
    print(f'chunk_idx={chunk_idx} rel_ms={rel} data_len={len(data)} first_chars={data[:80]}', flush=True)
    chunk_idx += 1
    if data == '[DONE]':
        break

total = int((time.monotonic() - t0) * 1000)
print(f'total_ms={total}', flush=True)
