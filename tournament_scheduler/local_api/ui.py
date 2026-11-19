"""Minimal same-origin browser shell. The bootstrap token arrives in the URL *fragment*
(never sent to the server or logged) and is exchanged once for an HttpOnly session."""

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>RVV operate</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'">
<style>body{font:16px system-ui;margin:2rem auto;max-width:48rem;padding:0 1rem}pre{white-space:pre-wrap}</style>
</head><body><h1>RVV operate</h1><p id="s">Connecting…</p><pre id="o"></pre><script>
(async()=>{const t=location.hash.slice(1);history.replaceState(null,"",location.pathname);
const s=document.getElementById("s"),o=document.getElementById("o");
if(!t){s.textContent="Open this page from the operate launcher.";return}
const r=await fetch("/api/v1/session/bootstrap",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token:t})});
if(!r.ok){s.textContent="Session could not be started.";return}
const b=(await r.json()).result;s.textContent="Connected as "+b.operator;
const c=await fetch("/api/v1/capabilities");o.textContent=JSON.stringify((await c.json()).result,null,2)})();
</script></body></html>"""


def page() -> bytes:
    return _PAGE.encode()
