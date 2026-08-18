with open("qobuz_dl/lyrics_engine.py", "r") as f:
    content = f.read()

# Add requests HTTPAdapter import if requests is imported
# Actually we can just do it inline where the session is created or in __init__
init_replace = """        self.session = session or requests.Session()"""
init_new = """        if session:
            self.session = session
        else:
            self.session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)"""

content = content.replace(init_replace, init_new)

with open("qobuz_dl/lyrics_engine.py", "w") as f:
    f.write(content)
