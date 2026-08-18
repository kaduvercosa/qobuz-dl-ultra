with open("qobuz_dl/qopy.py", "r") as f:
    content = f.read()

content = content.replace("httpx.RequestError", "httpx.HTTPError")

with open("qobuz_dl/qopy.py", "w") as f:
    f.write(content)
