with open("qobuz_dl/qopy.py", "r") as f:
    content = f.read()

content = content.replace('r = await self.session.request(method, self.base + epoint, **req_kwargs)\n                    if epoint', 'r = await self.session.request(method, self.base + epoint, **req_kwargs)\n                if epoint')

content = content.replace('r.status_code_code', 'r.status_code')

with open("qobuz_dl/qopy.py", "w") as f:
    f.write(content)
