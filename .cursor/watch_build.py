import base64, json, sys, time, urllib.request, yaml

cfg = yaml.safe_load(open("/home/Toby/.config/mac-k3d/worker.yaml"))
a = cfg["jenkins_agent"]
base = a["controller_url"].rstrip("/")
auth = base64.b64encode(f'{a["api_user"]}:{a["api_token"]}'.encode()).decode()


def get(path, raw=False):
    req = urllib.request.Request(base + path, headers={"Authorization": "Basic " + auth})
    body = urllib.request.urlopen(req, timeout=30).read()
    return body.decode("utf-8", "replace") if raw else json.loads(body)


job, number = sys.argv[1], int(sys.argv[2])
deadline = time.time() + float(sys.argv[3]) if len(sys.argv) > 3 else None
while True:
    try:
        b = get(f"/job/{job}/{number}/api/json?tree=building,result,duration")
    except Exception as exc:
        print("poll error", exc, flush=True)
        b = {"building": True}
    if not b.get("building"):
        print("RESULT", b.get("result"), b.get("duration", 0) // 1000, "s", flush=True)
        break
    if deadline and time.time() > deadline:
        print("STILL RUNNING", flush=True)
        break
    time.sleep(30)
