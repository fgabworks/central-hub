import json
import urllib.request

base = "http://127.0.0.1:8080"
req = urllib.request.Request(base + "/api/workspace-console/terminal/sessions")
data = json.loads(urllib.request.urlopen(req, timeout=20).read())
n = 0
for s in data.get("sessions") or []:
    r = urllib.request.Request(
        base + "/api/workspace-console/terminal/sessions/" + s["id"] + "?confirm=1",
        method="DELETE",
    )
    urllib.request.urlopen(r, timeout=20).read()
    n += 1
print("cleared", n)
